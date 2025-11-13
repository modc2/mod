from auth import UserAuth

def test_auth():
    auth = UserAuth()
    
    # Test registration
    result = auth.register('testuser', 'password123')
    print(f'Register: {result}')
    assert result['success'], 'Registration failed'
    
    # Test sign in
    result = auth.sign_in('testuser', 'password123')
    print(f'Sign in: {result}')
    assert result['success'], 'Sign in failed'
    token = result['token']
    
    # Test get user header
    result = auth.get_user_header(token)
    print(f'User header: {result}')
    assert result['success'], 'Get user header failed'
    
    # Test set key instance
    result = auth.set_key_instance(token, 'api_key', 'my_secret_key')
    print(f'Set key: {result}')
    assert result['success'], 'Set key failed'
    
    # Test get key instance
    result = auth.get_key_instance(token, 'api_key')
    print(f'Get key: {result}')
    assert result['success'] and result['value'] == 'my_secret_key', 'Get key failed'
    
    # Test sign out
    result = auth.sign_out(token)
    print(f'Sign out: {result}')
    assert result['success'], 'Sign out failed'
    
    # Test verify after sign out
    result = auth.get_user_header(token)
    print(f'Verify after sign out: {result}')
    assert not result['success'], 'Token should be invalid after sign out'
    
    print('All tests passed!')

if __name__ == '__main__':
    test_auth()