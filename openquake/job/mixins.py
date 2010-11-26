def loader(target, mixin):
    """ Load the mixin into the target's class """
    target.__class__.__bases__ += (mixin,)
    return target

def unloader(target, mixin):
    """ Unload the mixin from the target's class __bases__"""
    bases = list(target.__class__.__bases__)
    bases.remove(mixin)
    target.__class__.__bases__ = tuple(bases)


class Mixin:
    mixins = {}
    def __init__(self, target, mixin, key=""):
        self.key = key.upper() + "_CALCULATION_MODE"
        self.target = target
        self.mixin = mixin

    def __enter__(self):
        return self._load()

    def __exit__(self, *args):
        self._unload()

    def _load(self):
        if issubclass(self.mixin, Mixin):
            self._load_proxied_mixin()

        return loader(self.target, self.mixin)

    def _unload(self):
        if issubclass(self.mixin, Mixin):
            self._unload_proxied_mixin()

        return unloader(self.target, self.mixin)

    def _load_proxied_mixin(self):
        calc_mode = self.target.params[self.key]
        loader(self.target, self.mixin.mixins[calc_mode]['mixin'])

    def _unload_proxied_mixin(self):
        calc_mode = self.target.params[self.key]
        unloader(self.target, self.mixin.mixins[calc_mode]['mixin'])

    @classmethod
    def ordered_mixins(cls):
        """ Return a list of mixins sorted by the order value specified at 
        registration """

        return [(k, v['mixin'])
                 for (k, v)
                 in sorted(cls.mixins.items(), key=lambda x: x[1]['order'])]

    @classmethod
    def register(cls, key, mixin, order=0):
        """ Register a new mixin. We expect a string key, a class, and an 
        optional order. The order is really only optional in the mixin 
        proxies."""
        if not key in cls.mixins:
            cls.mixins[key] = {'mixin': mixin, 'order': order }
