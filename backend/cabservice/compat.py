def patch_django_template_context_copy():
    try:
        from django.template.context import BaseContext
    except Exception:
        return

    def copy_base_context(self):
        duplicate = self.__class__.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate

    BaseContext.__copy__ = copy_base_context
