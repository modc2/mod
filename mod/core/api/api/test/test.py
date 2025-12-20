 
import mod as m 

Api = m.mod('api')

class TestApi(Api):
    def test_call(self):
            key = m.key()
            address = key.address
            dest = m.key('test').address
            fn = 'store/ls'
            api = m.mod('api')()
            params = {}
            call_data = api.call_data(fn=fn, params=params)
            signature = key.sign(call_data, mode='str')
            assert m.verify(call_data, signature, address), "Invalid signature"
            response = api.call(fn=fn, params=params, sync=1)
            assert isinstance(response, list), f"Response is not a dictionary {response}"
            return True
