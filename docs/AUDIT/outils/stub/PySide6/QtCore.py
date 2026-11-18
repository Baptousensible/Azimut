def QT_TRANSLATE_NOOP(context, source, *a): return source
class QCoreApplication:
    @staticmethod
    def translate(context, source, *a, **k): return source
    @staticmethod
    def instance(): return None
class QLibraryInfo:
    class LibraryPath: TranslationsPath = 0
    @staticmethod
    def path(p): return "/nonexistent"
class QLocale:
    class Language: C = 0
    @staticmethod
    def system(): return QLocale()
    def uiLanguages(self): return []
    @staticmethod
    def setDefault(l): pass
    def __init__(self, *a): pass
    def language(self): return 0
class QTranslator: 
    def __init__(self, *a): pass
class QObject:
    def __init__(self, *a, **k): pass
def Signal(*a, **k): return None
