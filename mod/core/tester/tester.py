    
import mod as m
class Test:
    description = """
    i test stuff
    """
    def forward(self, mod=None, timeout=50, modules=[ 'server', 'store','key', 'ipfs', 'auth', 'executor']):
        """
        Test the mod 
        """
        if mod == None:
            test_results ={}
            print(f'Testing modules: {modules}')
            for mod in modules:
                print(f'Testing mod: {mod}')
                test_results[mod] = self.forward(mod=mod, timeout=timeout)
            return test_results
        else:
            fn2result = {}
            fns = self.test_fns(mod)
            for fn in fns:
                fn_name = fn.__name__
                try:
                    fn2result[fn_name] = fn()
                    print(f'TestResult({fn_name}): {fn2result[fn_name]}')
                except Exception as e:
                    m.print(f'TestError({e})')
                    fn2result[fn_name] = m.detailed_error(e)
            return fn2result

    test = forward

    def has_test_module(self, mod, verbose=False):
        """
        Check if the mod has a test mod
        """
        return m.mod_exists(mod + '.test')

    def has_test_fns(self, mod):
        return bool('test' in m.fns(mod))
    def test_module(self, mod='mod', timeout=50):
        """
        Test the mod
        """
        for fn in self.test_fns(test):
            print(f'Testing({fn})')
            future = self.submit(getattr(test, fn), timeout=timeout)
            futures += [future]
        results = []
        for future in self.as_completed(futures, timeout=timeout):
            print(future.result())
            results += [future.result()]
        return results

    testmod = test_module

    def test_fns(self, mod='mod'):
        if self.has_test_module(mod):
            mod = mod + '.test'
        obj = m.mod(mod)()
        test_fns = []
        for fn in dir(obj):
            if fn.startswith('test_') or fn == 'test':
                fn_obj = getattr(obj, fn)
                test_fns.append(fn_obj)
        return test_fns


    def has_test(self, mod=None, verbose=False):
        """
        Check if the mod has a test mod or test functions
        """
        try:
            return self.has_test_module(mod) or self.has_test_fns(mod)
        except Exception as e:
            if verbose:
                m.print(f'Error checking tests for {mod}: {e}')
        return False


    def test_mods(self, search=None, verbose=False, **kwargs):
        test_mods = []
        mods =  m.mods(search=search, **kwargs)
        for mod in mods:
            if verbose:
                m.print(f'Checking mod: {mod}')
            if self.has_test(mod, verbose=verbose):
                test_mods.append(mod)
        return test_mods