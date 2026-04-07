from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class SupportedSiteEntry:
    name: str
    url: str
    capabilities: str
    auth: str
    tooltip_text: str
    section: str

    @property
    def search_text(self) -> str:
        return " ".join(
            part.lower()
            for part in (self.name, self.url, self.capabilities, self.auth, self.section)
            if part
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, str]) -> "SupportedSiteEntry":
        return cls(
            name=payload.get("name", ""),
            url=payload.get("url", ""),
            capabilities=payload.get("capabilities", ""),
            auth=payload.get("auth", ""),
            tooltip_text=payload.get("tooltip_text", ""),
            section=payload.get("section", ""),
        )


@dataclass(slots=True)
class SupportedSitesPayload:
    sites: list[SupportedSiteEntry]
    fetched_at: str
    source_url: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sites": [site.to_dict() for site in self.sites],
            "fetched_at": self.fetched_at,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SupportedSitesPayload":
        raw_sites = payload.get("sites", [])
        sites = [
            SupportedSiteEntry.from_dict(item)
            for item in raw_sites
            if isinstance(item, dict)
        ]
        return cls(
            sites=sites,
            fetched_at=str(payload.get("fetched_at", "")),
            source_url=str(payload.get("source_url", "")),
        )
