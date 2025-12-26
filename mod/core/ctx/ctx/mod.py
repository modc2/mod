
import mod as m
class Mod:
    description = """
    Base mod template
    """
    
    def forward(self,mod = 'mod'): 
        ctx = {
            'code': m.code(mod),
            'readme': self.readme(mod),
            'mod': mod
        }
        return ctx

    def readme(self, mod = 'mod'):
        dp = m.dp(mod)
        for f in m.files(dp, depth=1):
            print(f)
            if f.split('/')[-1].lower().startswith('readme'):
                return m.get_text(f)
        return ""