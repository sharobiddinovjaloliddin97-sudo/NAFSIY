from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.db.database import Base


class Surah(Base):
    __tablename__ = "surahs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name_ar: Mapped[str] = mapped_column(String(200))
    name_en: Mapped[str] = mapped_column(String(200))
    ayah_count: Mapped[int]
    ayahs: Mapped[list["Ayah"]] = relationship(back_populates="surah")


class Ayah(Base):
    __tablename__ = "ayahs"

    id: Mapped[int] = mapped_column(primary_key=True)
    surah_id: Mapped[int] = mapped_column(ForeignKey("surahs.id"))
    number: Mapped[int]
    arabic_text: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("surah_id", "number"),)

    surah: Mapped["Surah"] = relationship(back_populates="ayahs")
    translations: Mapped[list["Translation"]] = relationship(back_populates="ayah")


class Translation(Base):
    __tablename__ = "translations"

    id: Mapped[int] = mapped_column(primary_key=True)
    ayah_id: Mapped[int] = mapped_column(ForeignKey("ayahs.id"))
    language: Mapped[str] = mapped_column(String(10))
    edition: Mapped[str] = mapped_column(String(100))
    translator: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("ayah_id", "edition"),)

    ayah: Mapped["Ayah"] = relationship(back_populates="translations")
