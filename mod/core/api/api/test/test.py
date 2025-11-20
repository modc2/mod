 
import mod as m 

Api = m.mod('api')

class TestApi(Api):
    def test_call_signature(self):
            key = m.key()
            address = key.address
            dest = m.key('test').address
            mod = 'Balances'
            fn = 'transfer_keep_alive'
            params = {'dest': dest, 'value': 0.1 * 10**12}
            signature_payload = m.call('api/get_signature_payload',
                params=dict(mod=mod,
                fn=fn,
                params=params,
                address=address,
                era=None)
            )

            signature = key.sign(signature_payload, mode='str')
            assert m.verify(signature_payload, signature, address), "Invalid signature"
            response = m.call('api/call_with_signature',
                params=dict(mod=mod,
                fn=fn,
                params=params,
                address=address,
                signature=signature
                )
            )
            return response
