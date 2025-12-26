 
import mod as m

Server = m.mod('server')
class ServerTestMixin(Server):
    def test_server(self, 
                        server = 'mod', 
                        key="server", 
                        trials=10, 
                        sleep_interval=2):
            m.serve(server, key=key)
            print(f'testing server {server} with')
            info = {}
            for i in range(trials): 
                print(f'testing server {server}, trial {i+1}/{trials}...')
                try:
                    info = m.fn('client/call')(server+'/info')
                except Exception as e:
                    m.print(f'warning: failed to connect to server {server}, trial {i}/{trials}, error: {e}')
                    m.sleep(sleep_interval)
                    continue
                print(f'info: {info}')
                if 'key' in info: 
                    assert info['key'] == m.key(key).ss58_address, f"Server key {info['key']} does not match expected {m.key(key).ss58_address}"
                    return {'success': True, 'msg': 'server test passed'}

            raise Exception(f"Failed to connect to server {server} after {trials} trials, last info: {info}")

    def test_executor(self):
        return m.mod('executor')().test()

    def test_auth(self, auths=['auth.jwt', 'auth']):
        for auth in auths:
            print(f'testing {auth}')
            m.test(auth)
        return {'success': True, 'msg': 'server test passed', 'auths': auths}

    def test_user(self,  mod='api', user='test', update:bool = False):  
        """
        check if the address is blacklisted
        """
        gate = m.mod('gate')()
        user  = m.key(user).address
        gate.add_user(mod, user)
        assert user in gate.users(mod), f"Failed to add {user} to blacklist"
        gate.rm_user(mod, user, update=update)
        assert user not in gate.users(mod) and not gate.is_user(mod, user), f"Failed to remove {user}"
        return {'user': user , 'users': gate.users(mod)}
