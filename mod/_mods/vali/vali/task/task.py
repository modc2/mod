class Task:
    def forward(self, mod,  fn='info', params={}) -> float:
        mod = c.connect(mod) if isinstance( mod, str) else mod
        result =  getattr(mod, fn)(**params)
        if 'url' in result and isinstance(result, dict) :
            score =  1
        else:
            score =  0
        return float(score)
