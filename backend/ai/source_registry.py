"""Reviewed allowlist, not a discovery feed. Changes require provenance review."""
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json

from backend.core.config import settings


class LicenseState(StrEnum):
    LICENSED = "licensed"
    PERMISSION_PENDING = "permission_pending"
    RESTRICTED = "restricted"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Edition:
    id: str
    provider: str
    language: str
    kind: str
    attribution: str
    license_state: LicenseState
    license_url: str
    license_note: str
    review_date: str = "2026-10-31"


REGISTRY = {
    "quran-uthmani": Edition("quran-uthmani", "AlQuran Cloud", "ar", "quran",
        "Uthmani Arabic via AlQuran Cloud; Tanzil Quran Text © 2007–2021 Tanzil Project, CC BY 3.0; Quran Academy curation",
        LicenseState.LICENSED, "https://alquran.cloud/terms-and-conditions",
        "Provider terms allow faithful display; preserve every word and diacritic. "
        "Tanzil attribution and text license: https://tanzil.net/docs/Text_License"),
    "en.itani": Edition("en.itani", "AlQuran Cloud", "en", "translation",
        "Translation by Talal Itani, ClearQuran.com", LicenseState.LICENSED,
        "https://clearquran.com/download/", "CC BY-ND 4.0 with rights-holder additional permissions; unchanged wording and credit."),
    "en.sahih": Edition("en.sahih", "AlQuran Cloud", "en", "translation",
        "Saheeh International", LicenseState.PERMISSION_PENDING,
        "https://alquran.cloud/terms-and-conditions", "Provider availability is not edition-specific permission."),
    "ru.kuliev": Edition("ru.kuliev", "AlQuran Cloud", "ru", "translation",
        "Elmir Kuliev", LicenseState.PERMISSION_PENDING,
        "https://tanzil.net/trans/", "Commercial/application rights require review."),
    "uz.sodik": Edition("uz.sodik", "AlQuran Cloud", "uz", "translation",
        "Muhammad Sodiq Muhammad Yusuf", LicenseState.PERMISSION_PENDING,
        "https://tanzil.net/trans/", "No permission verified; do not fetch text."),
    "tafsiri-hilol": Edition("tafsiri-hilol", "Hilol Nashr", "uz", "tafsir",
        "Shaykh Muhammad Sodiq Muhammad Yusuf", LicenseState.RESTRICTED,
        "", "Explicit rights-holder permission required. No adapter, scraping or import."),
    "ummah-hadith": Edition("ummah-hadith", "UmmahAPI", "en", "hadith",
        "UmmahAPI; upstream fawazahmed0/hadith-api", LicenseState.PERMISSION_PENDING,
        "https://github.com/fawazahmed0/hadith-api/blob/1/LICENSE",
        "Upstream software is Unlicense; individual translation rights and edition/numbering provenance remain unresolved."),
}


def permitted(identifier):
    entry = REGISTRY.get(identifier)
    return entry is not None and entry.license_state == LicenseState.LICENSED


def translation_editions(language=None):
    return [entry for entry in REGISTRY.values() if entry.kind == "translation"
            and permitted(entry.id) and (language is None or entry.language == language)]


def arabic_edition():
    identifier = settings.QURAN_ARABIC_EDITION
    entry = REGISTRY.get(identifier)
    if not permitted(identifier) or entry.kind != "quran" or entry.language != "ar":
        raise ValueError("Configured Arabic Quran edition has not been reviewed")
    return identifier


def content_digest(record):
    fields = ("type", "reference", "provider", "arabic_edition", "translation_edition",
              "numbering_system", "arabic", "translation", "grade", "grade_authority")
    return hashlib.sha256(json.dumps({key: record.get(key, "") for key in fields},
                                    ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def registry_metadata():
    return [asdict(entry) for entry in REGISTRY.values()]
