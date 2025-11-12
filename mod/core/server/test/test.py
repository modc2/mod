 
import mod as m

class ServerTestMixin(m.mod('server')):
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
                    info = m.call(server+'/info')
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
            m.mod(auth)().test()
        return {'success': True, 'msg': 'server test passed', 'auths': auths}


    def test_blacklist(self,   update:bool = False):  
        """
        check if the address is blacklisted
        """
        key = m.get_key('test').ss58_address
        self.blacklist_user(key)
        assert key in self.blacklist(), f"Failed to add {key} to blacklist"
        self.unblacklist_user(key, update=update)
        assert key not in self.blacklist(), f"Failed to remove {key} from blacklist"
        return {'blacklist': True, 'user': key , 'blacklist': self.blacklist()}
