from typing import *
import os
import hashlib
import re
import pandas as pd
import json
import inspect
import time
import mod as m
from mod.core.server.executor.worker import SandboxWorker, DockerWorker, WorkerPool

print = m.print

# Whitelist pattern for function names: alphanumeric, underscores, slashes, dots, hyphens
FN_NAME_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_/.-]{0,200}$')

class Gate:

    def __init__(self, path = '~/.mod/server', auth='auth.base',mod='api', paywall=None, serializer='serializer', sandbox='docker', meter=True, **_kwargs):
        """
        Initialize the Gate class
        params:
            path: the path to store the gate data
            auth: the auth module to use
            paywall: optional x402 payment gate instance (has gate_check(fn, headers) method)
            sandbox: 'docker' (default), 'pool' for persistent workers, or 'subprocess' for one-shot
            meter: enable usage metering (default True)
        """
        self.store = m.mod('store')(path)
        self.auth = m.mod(auth)()
        self.paywall = paywall
        self.serializer = m.mod(serializer)()
        if sandbox == 'docker':
            self.sandbox = DockerWorker(max_workers=10)
            print('Gate: using Docker sandbox (container isolation)', color='green')
        elif sandbox == 'pool':
            self.sandbox = WorkerPool(min_workers=2, max_workers=10)
            print('Gate: using WorkerPool (persistent background workers)', color='green')
        else:
            self.sandbox = SandboxWorker(max_workers=10)
        # Usage metering
        self.meter = None
        if meter:
            try:
                self.meter = m.mod('meter')()
                print('Gate: usage metering enabled', color='green')
            except Exception:
                print('Gate: meter module not found, metering disabled', color='yellow')
        self.roles_path = self.store.get_path('roles')
        if len(self.roles()) < 2:
            self.ensure_role_map()
        self.set_mod(mod=mod)
    

    # Functions that run in-process (API-level, not user module code)
    UNSANDBOXED_FNS = {
        'mods', 'mod', 'info', 'txs', 'h', 'tasks', 'kill_task', 'reset_tasks',
        'users', 'call', 'schema', 'content', 'config', 'versions', 'edit',
        'reg', 'update', 'fork', 'new', 'rm', 'n', 'transfer', 'set_public',
        'token', 'logs', 'namespace', 'serve', 'stop', 'get', 'put',
        # Metering, billing, load balancing, paywall (mod paths)
        'meter', 'balancer', 'paywall',
        # Worker management (owner-only via RBAC)
        'worker_status', 'worker_scale', 'worker_set_limits',
    }

    def forward(self, fn:str, headers:dict, params:dict, mod:Any=None) -> dict:
        """
        process the request
        Routes module function calls through SandboxWorker for isolation.
        API-level functions (mods, txs, etc.) run in-process.
        """
        mod = mod or self.mod
        # ── Validate function name ──
        assert isinstance(fn, str) and fn != '', "Function name cannot be empty"
        assert FN_NAME_RE.match(fn) and '..' not in fn, f"Invalid function name: {fn}"

        print(f'Gate forwarding request to function: {fn}', color='green')
        params = json.loads(params) if isinstance(params, str) else params
        try:
            info = mod.info if isinstance(mod.info, dict) else mod.info()
        except Exception:
            info = {}

        # ── Resolve actual function name ──
        # The client wraps path calls (e.g. 'bridge/info') as fn='call' with
        # the real function in params.  Extract the leaf function name for
        # auth checks so PUBLIC_FNS matching works correctly.
        actual_fn = fn
        if fn in ('call', 'forward') and isinstance(params, dict):
            inner = params.get('fn', fn)
            # 'bridge/info' → 'info',  'info' → 'info'
            actual_fn = inner.split('/')[-1] if isinstance(inner, str) else fn
        elif '/' in fn:
            # Direct path calls: 'api/mods' → 'mods'
            actual_fn = fn.split('/')[-1]

        # ── Authenticate ──
        authenticated = False
        role = None
        try:
            # Check for token in params (client puts it there for path calls)
            if isinstance(params, dict) and params.get('token') and not headers.get('token'):
                headers = dict(headers) if not isinstance(headers, dict) else headers
                headers['token'] = params['token']
            headers = self.auth.verify(headers)
            authenticated = True
            role = self.get_role(headers['key'])
        except Exception as auth_err:
            # Auth failed — only allow through if fn is in PUBLIC_FNS
            headers = headers if isinstance(headers, dict) else {}
            headers['key'] = headers.get('key', '')
            role = 'public'
            print(f'Auth failed ({auth_err}), checking public access for {fn}', color='yellow')

        # ── Authorize ──
        if role == 'owner' and authenticated:
            print(f'ATTENTION: owner({headers["key"]}) is calling {fn}', color='green')
        elif not authenticated and actual_fn in self.PUBLIC_FNS:
            # Public functions are always allowed without auth
            print(f'Public access granted for {fn} ({actual_fn})', color='green')
        else:
            mod_fns = info.get('fns', []) if isinstance(info, dict) else []
            if mod_fns:
                assert actual_fn in mod_fns, f"Function {actual_fn} not in fns={mod_fns}"
            role_data = self.role_data(role) if role else self.role_data('public')
            role_fns = role_data.get('fns', [])
            if role_fns and '*' not in role_fns:
                assert actual_fn in role_fns, f"Function {actual_fn} not permitted for role={role or 'public'}, allowed={role_fns}"
            if not authenticated:
                assert actual_fn in self.PUBLIC_FNS, f"Authentication required for {actual_fn}"
        self.print_request({'fn': fn, 'params': params, 'client': headers.get('key', ''), 'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())})
        # Payment gate check (x402 paywall)
        if self.paywall and hasattr(self.paywall, 'gate_check'):
            paywall_result = self.paywall.gate_check(fn, headers)
            if paywall_result is not None:
                print(f'Payment required for {fn}: {paywall_result}', color='red')
                return paywall_result

        # Determine if this is a module function call that should be sandboxed
        # Module calls have '/' (e.g., 'ssh/keys') and aren't in the unsandboxed set
        # Use actual_fn (leaf name) to check against whitelist
        is_module_call = '/' in fn and actual_fn not in self.UNSANDBOXED_FNS

        t0 = time.time()
        status = 'success'
        try:
            if fn in ('call', 'forward') and isinstance(params, dict) and '/' in str(params.get('fn', '')):
                # Module call via 'call' wrapper — resolve and execute directly
                # (gate already verified auth/public access above)
                inner_fn = params['fn']
                inner_params = params.get('params', {})
                if isinstance(inner_params, str):
                    inner_params = json.loads(inner_params)
                fn_obj = self.get_fn_obj(inner_fn, mod=mod)
                result = fn_obj(**inner_params) if callable(fn_obj) else fn_obj
            elif is_module_call and actual_fn not in self.UNSANDBOXED_FNS:
                # Execute in sandboxed subprocess
                print(f'Gate: sandboxed execution for {fn}', color='yellow')
                result = self.sandbox.run(fn_path=fn, params=params, timeout=120)
            else:
                # In-process execution for API-level functions
                fn_obj = self.get_fn_obj(fn, mod=mod)
                result = fn_obj(**params) if callable(fn_obj) else fn_obj
            if isinstance(result, bytes):
                result = result.decode('utf-8')
        except Exception as e:
            status = 'error'
            duration = time.time() - t0
            if self.meter:
                try:
                    self.meter.record(
                        user=headers.get('key', ''),
                        fn=fn,
                        duration=duration,
                        status=status,
                        params_size=len(json.dumps(params)) if params else 0,
                    )
                except Exception:
                    pass
            raise

        # Record usage
        duration = time.time() - t0
        if self.meter:
            try:
                import sys
                result_size = sys.getsizeof(result) if result is not None else 0
                self.meter.record(
                    user=headers.get('key', ''),
                    fn=fn,
                    duration=duration,
                    status=status,
                    params_size=len(json.dumps(params)) if params else 0,
                    result_size=result_size,
                )
            except Exception:
                pass

        return result

    # ── Worker management (owner-only) ─────────────────────────────────

    def worker_status(self) -> dict:
        """Get the status of all sandbox workers."""
        return self.sandbox.status()

    def worker_scale(self, n: int) -> dict:
        """Manually scale workers to n (clamped to limits)."""
        return self.sandbox.scale(n)

    def worker_set_limits(self, max_workers: int = None, idle_timeout: int = None) -> dict:
        """Set worker pool limits. max_workers controls capacity, idle_timeout (seconds) controls auto-shutdown."""
        kwargs = {}
        if max_workers is not None:
            kwargs['max_workers'] = max_workers
        if idle_timeout is not None:
            kwargs['idle_timeout'] = idle_timeout
        return self.sandbox.set_limits(**kwargs)

    def get_role(self, user:str) -> str:
        """
        get the role for a user
        """
        role2data = self.role2data()
        for role, data in role2data.items():
            if 'users' in data and user.lower() in data['users']:
                return role
        return None

    def is_generator(self, obj):
        """
        Is this shiz a generator dawg?
        """
        if not callable(obj):
            result = inspect.isgenerator(obj)
        else:
            result =  inspect.isgeneratorfunction(obj)
        return result

    def set_mod(self, mod:Any=None):
        if isinstance(mod, str):
            mod = m.mod(mod)()
        elif mod is None:
            mod = m.mod('mod')()
        elif not hasattr(mod, 'info'):
            mod = m.mod('mod')(mod)
        self.mod = mod 
        return self.mod

    def print_request(self, request: dict):
        """
        print the request nicely
        """
        fn = request.get('fn', '')
        params = request['params'] if 'params' in request else {}
        client = request.get('client', '')
        right_buffer = '>'*64
        left_buffer = '<'*64
        print(right_buffer, color='blue')
        print(f"""Request\t""" , color='blue')
        print(left_buffer)
        print_params = {'fn': fn, 'params': params, 'client': client,'time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}
        # side ways dataframe where each param is a row
        df = pd.DataFrame(print_params.items(), columns=['param', 'value'])
        print(df.to_string(index=False), color='blue')
        print(right_buffer, color='blue')

    def get_fn_obj(self, fn:str, mod:Any) -> Any:
        if not hasattr(self, '_obj_cache'):
            self._obj_cache = {}
        if '/' in fn:
            if fn in self._obj_cache:
                fn_obj = self._obj_cache[fn]
                print(f'Using cached function object for {fn}', color='green')
            else:
                temp_mod = fn.split('/')[0]
                fn = '/'.join(fn.split('/')[1:])
                if hasattr(self.mod, temp_mod):
                    mod_obj = getattr(mod, temp_mod)
                    fn_obj = getattr(mod_obj, fn)
                else: 
                    if m.mod_exists(temp_mod):
                        mod_obj = m.mod(temp_mod)()
                        fn_obj = getattr(mod_obj, fn)
                self._obj_cache[fn] = fn_obj
        else:
            fn_obj = getattr(self.mod, fn) # get the function object from the mod
        return fn_obj

    def add_user_max(self, max_users:int= 100):
        """
        gate if the address is usersed
        """
        self.store.put('user_max' , max_users)
        return self.user_max()

    def user_max(self,default:bool = 100) -> int:
        """
        gate if the address is usersed
        """
        return self.store.get('user_max' , default)

    def add_user(self, user:str, role='public', update:bool = False ):
        """
        gate if the address is usersed
        """
        role_data = self.role_data(role)
        role_data['users'] = list(map(lambda x:x.lower(), set(role_data.get('users', []) + [user])))
        self.save_role_data(role, role_data)
        if self.is_user(user, role): 
            return {'status': 'success', 'message': f'User {user} added to role {role}'}
        return self.role_data(role)

    def owner_key(self) -> str:
        if not hasattr(self, 'owner_address'):
            self.owner_address = m.key().ss58_address
        return self.owner_address


    def user2role(self) -> Dict[str, str]:
        """
        preprocess if the address is usersed
        """
        role2data = self.role2data()
        user2role = {}
        for role, data in role2data.items():
            if 'users' in data:
                for user in data['users']:
                    user2role[user] = role
        return user2role

    def users(self, role:str = None, update:bool = False):
        """
        preprocess if the address is usersed
        """
        role_data = self.role_data(role)
        return role_data.get('users', [])

    def users_path(self, role:str) -> str:
        """
        preprocess if the address is usersed
        """
        return f'users/{role}'

    def resolve_role(self, role='public'): 
        return role or 'public'

    def reset_roles(self):
        """
        reset the roles
        """
        self.store.rm(self.roles_path)
        return self.ensure_role_map()

    def rm_user(self, user:str, role = None, update:bool = False):
        """
        preprocess if the address is usersed
        """
        role_data = self.role_data(role)
        users = role_data.get('users', [])
        if user in users:
            users.remove(user)
        role_data['users'] = users
        self.save_role_data(role, role_data)
        assert not self.is_user(user, role), f"Failed to remove user {user} from role {role}"
        return self.role_data(role)

    def is_user(self,  user:str, role = None) -> bool:
        """
        preprocess if the address is usersed
        """
        role = self.resolve_role(role)
        return user in self.users(role)

    role2data_path = 'role2data'

    def role_data(self, role:str = None) -> Dict[str, Any]:
        """
        get the role to data mapping
        """
        role = self.resolve_role(role)
        role_data  =  self.store.get(self.role_data_path(role), {})
        role_data['users'] = list(map(lambda x:x.lower(), role_data.get('users', [])))
        return role_data
    
    def roles(self) -> List[str]:
        """
        get the roles
        """
        if not os.path.exists(self.roles_path):
            os.makedirs(self.roles_path)
        return list(map(lambda x: x.split('.json')[0], os.listdir(self.roles_path)))

    def save_role_data(self, role:str, data:dict):
        """
        save the role data
        """
        role = self.resolve_role(role)
        self.store.put(self.role_data_path(role), data)
        return data

    def role_data_path(self, role:str) -> str:
        """
        get the role data path
        """
        role = self.resolve_role(role)
        return self.roles_path + '/' + role

    def add_role(self, role:str = 'owner',  users:List[str] = None, fns:dict = []):
        """
        add a role
        """
        role_data = self.role_data()
        role_data['users'] = users or []
        if isinstance(fns, str):
            fns = [fns]
        if  role == 'owner':
            fns = ['*']
            role_data['users'].append(self.owner_key())
        else:
            assert '*' not in fns, "Only owner role can have '*' permission"
        role_data['fns'] = fns
        return self.save_role_data(role, role_data)

    def rm_role(self, role:str):
        return self.store.rm(self.role_data_path(role))
        
    def role2data(self) -> Dict[str, Any]:
        roles = self.roles()
        role2data = {}
        for role in roles:
            role2data[role] = self.role_data(role)
        return role2data

    def add_permission(self, role:str, fn:str):
        """
        add a permission to a role
        """
        role2data = self.role2data()
        if role not in role2data:
            role2data[role] = {'fns': []}
        if 'fns' not in role2data[role]:
            role2data[role]['fns'] = []
        if fn not in role2data[role]['fns']:
            role2data[role]['fns'].append(fn)
        self.store.put(self.role2data_path, role2data)
        return role2data

    def delegations(self):
        """
        get the delegations
        """
        return self.store.get('delegations', {})

    def update_delegations(self, delegations:dict):
        """
        update the delegations
        """
        self.store.put('delegations', delegations)
        return delegations

    def set_delegations(self, delegator:str, delegatees:List[str]):
        """
        set a delegation
        """
        delegations = self.delegations()
        delegations[delegator] = delegatees
        self.store.put('delegations', delegations)
        return delegations

    def rm_delegation(self, delegator:str):
        """
        remove a delegation
        """
        delegations = self.delegations()
        if delegator in delegations:
            del delegations[delegator]
        self.store.put('delegations', delegations)
        return delegations
 

    ensure_roles = ['owner', 'public']
    # public users can read + edit in their peer space only
    PUBLIC_FNS = [
        'mod', 'mods', 'schema', 'content', 'files', 'exists', 'get',
        'user', 'users', 'user_keys', 'versions', 'registry',
        'edit', 'reg', 'reg_payload', 'token', 'fork', 'new',
        'balance', 'balances', 'get_balances',
        'app_namespace', 'app_status', 'app_owner', 'is_app_owner', 'app_logs',
        'serve_app', 'kill_app', 'new_app', 'edit_app', 'remove_app',
        # Read-only module endpoints (health, status, listings)
        'health', 'status', 'owner', 'contract_info', 'info',
        'description', 'readme',
        'in_snapshot', 'get_total_balances',
        'get_claims', 'claims_array', 'has_claimed', 'unclaimed',
        'get_commitments', 'get_commitment',
        'commit', 'update_commitment', 'claim',
    ]
    def ensure_role_map(self):
        """
        ensure that the owner role exists
        """
        role2data = self.role2data()
        for role in self.ensure_roles:
            if role not in role2data:
                if role == 'public':
                    self.add_role(role=role, fns=self.PUBLIC_FNS)
                else:
                    self.add_role(role=role)
            elif role == 'public':
                # always sync public fns
                rd = self.role_data('public')
                if rd.get('fns', []) != self.PUBLIC_FNS:
                    rd['fns'] = self.PUBLIC_FNS
                    self.save_role_data('public', rd)