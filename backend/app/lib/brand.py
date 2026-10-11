from dataclasses import dataclass


@dataclass
class BrandInfo:
    name: str = "Alethra"
    tagline: str = "Ideas, tested. Decisions, yours."
    tagline_zh: str = "想法接受檢驗，決定仍由你作。"
    product_descriptor: str = "Investment Research Workspace"
    product_descriptor_zh: str = "AI投資研究工作台"
    description: str = "Transform investment ideas into testable, traceable, and continuously reviewable research."
    description_zh: str = "把投資想法轉化為可測試、可追溯、可持續檢視的研究。"
    disclaimer: str = "This is a research tool. All results are for research reference only and do not represent future performance. We do not execute live trades, provide investment advice, or guarantee any returns."
    disclaimer_zh: str = "本系統是研究工具。所有結果僅供研究參考，不代表未來績效。我們不進行實盤交易、不提供投資建議、不保證任何收益。"
    disclaimer_short: str = "Research tool, not investment advice."
    disclaimer_short_zh: str = "研究工具，非投資建議。"


_brand = BrandInfo()


def get_brand() -> BrandInfo:
    return _brand
