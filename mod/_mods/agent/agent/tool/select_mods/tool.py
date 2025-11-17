import mod as m

class SeelctMods:
    def forward(self, query='most relevent tool that can store things', *extra_query, n=10):
        """
        read the content of a mod file
        
        """
        return m.fn('select_options/')(options=m.mods(), query=query)

    def test(self , mod='read_mod', n=1):
        """
        test the read_mod tool
        """
        assert mod in self.forward(query=f'find the tool that is called {mod}', n=n)[0]
        return True