"""Small, auditable query expansion; never transliterates sacred source text."""
import re
import unicodedata

APOSTROPHES = str.maketrans({c: "'" for c in "‘’ʻʼ`´ʹ"})
CYRILLIC = dict(zip("абвгдеёзийклмнопрстуфхцчшъыьэюяқғҳў", (
    "a","b","v","g","d","e","yo","z","i","y","k","l","m","n","o","p","r","s","t","u",
    "f","x","ts","ch","sh","'","i","","e","yu","ya","q","g'","h","o'")))
TOPICS = {
    "sabr": ("patience", "sabr", "терпение"),
    "namoz": ("prayer", "namoz", "молитва", "salat"),
    "niyat": ("intention", "niyat", "намерение"),
    "ilm": ("knowledge", "ilm", "знание"),
    "ro'za": ("fasting", "ro'za", "roza", "пост", "oruza"),
    "zakot": ("charity", "zakot", "zakat", "милостыня"),
    "ota-ona": ("parents", "ota-ona", "родители"),
    "rahmat": ("mercy", "rahmat", "милость"),
    "tavba": ("repentance", "tavba", "покаяние"),
}


def normalize_query(text, transliterate=False):
    value = unicodedata.normalize("NFC", text).casefold().translate(APOSTROPHES)
    if transliterate:
        value = "".join(CYRILLIC.get(char, char) for char in value)
    return " ".join(value.split())


def expand_queries(text):
    raw = normalize_query(text)
    latin = normalize_query(text, transliterate=True)
    result = []
    for variants in TOPICS.values():
        if any(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", raw)
               or re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", latin) for word in variants):
            result.append(variants[0])
    return result[:3]


def augment_plan(plan, question):
    """Respect exact references; supplement bounded planner queries with lexical hints."""
    hints = expand_queries(question)
    if not hints:
        return plan
    changes = {}
    for kind in ("quran", "hadith"):
        if not getattr(plan, kind + "_references"):
            changes[kind + "_queries"] = list(dict.fromkeys(hints + getattr(plan, kind + "_queries")))[:3]
    return plan.model_copy(update=changes)


def scholar_referral(question):
    value = normalize_query(question)
    latin = normalize_query(question, transliterate=True)
    if not re.search(r"\b(?:fatwa|fatvo|taloq|divorce|takfir|фетв\w*|развод\w*)\b|فتوى|طلاق|تكفير", value + " " + latin):
        return None
    if re.search(r"[\u0600-\u06ff]", value):
        return "لا أستطيع إصدار فتوى شخصية. يرجى استشارة عالم مؤهل مع بيان الظروف كاملة."
    if re.search(r"[а-я]", value) and not re.search(r"[ўқғҳ]|fatvo|taloq", value + latin):
        return "Я не могу выдавать личные фетвы. Обратитесь к квалифицированному учёному и изложите все обстоятельства."
    if re.search(r"\b(?:fatvo|taloq)\b", latin):
        return "NAFSIY shaxsiy fatvo bermaydi. Vaziyatni to‘liq tushuntirib, malakali olimga murojaat qiling."
    return "NAFSIY cannot issue a personal fatwa. Consult a qualified scholar with the full circumstances."
