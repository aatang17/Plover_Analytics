# -*- coding: utf-8 -*-
u"""What kind of institution a 5%-filer is, and which group it belongs to.

Two labels, both DERIVED and both applied at serve time — the extractor stores
only what the filing says (`business_ja`, `occupation_ja`, `holder_type_ja`).
Keeping the reading out of the stored row means changing a rule costs a
redeploy, never a re-extraction, and never rewrites a vintage. Same division of
labour as `facility_labels.py`.

  * **filer_type** — read from the filer's OWN 事業内容 (business description),
    which 88% of holder rows state, plus the filed 法人/個人 flag. It is not a
    guess from the name: a firm is called an asset manager here because it
    filed 投資運用業, and a holder whose filing states nothing is `not_stated`
    and renders as a gap.

  * **group** — the family a filing entity belongs to. This one CANNOT come
    from a filing: BlackRock files under sixteen EDINET codes, Fidelity under
    thirteen, Nomura under ten, and no document names the parent. So it is a
    hand-curated map, kept deliberately small, and every entry carries the name
    exactly as it appears in the filings so it can be checked. A code that is
    not in the map is its own group — never guessed from a shared word, because
    Sumitomo Corporation and Sumitomo Mitsui Banking are not one company and
    "Capital" is in the name of forty-eight unrelated firms.
"""
import re

# --- filer type -------------------------------------------------------------
#
# Ordered, first match wins, matched against the filed 事業内容. Order is
# load-bearing: an asset manager routinely lists a securities-business licence
# after its investment-management one (投資運用業、第二種金融商品取引業…), and
# reading it as a broker would file the largest fund houses in Japan under the
# wrong heading.
TYPE_RULES = (
    # Own-account first, and this ordering is the whole trick. Hikari Tsushin's
    # securities arm files 有価証券の保有管理及び投資運用 — it manages a book of
    # its own, and reading the 投資運用 at the end of that sentence as "asset
    # manager" would file a corporate holding vehicle among the fund houses.
    # Managing your own money is not managing anybody else's.
    ("investment_vehicle", u"有価証券の保有|有価証券の取得|有価証券の運用|有価証券の投資|"
                           u"自己の計算|資産管理会社|資産の保有"),
    ("asset_manager", u"投資顧問|投資助言|投資一任|投資信託|信託財産の運用|顧客資産|"
                      u"顧客またはファンド|ファンドの資産管理|アセットマネジメント|"
                      u"インベストメント・マネジメント|投資運用|資産運用|運用業|"
                      u"投資マネージャー|投資マネジャー"),
    # Not 投資業務: a company that files "investment business" alone is
    # investing its own money (花明, 大和PIパートナーズ), and is read below
    # as an investment vehicle.
    ("broker_dealer", u"証券業|金融商品取引業|第一種金融商品|証券取引|ブローカー|ディーラー"),
    # A trust bank lists its trust business FIRST (三菱ＵＦＪ信託銀行 files
    # 信託業務・銀行業務); りそな銀行 is a bank that also holds a trust
    # licence and lists 信託業務 fourth, so it is read as a bank.
    ("trust_bank",    u"^信託|信託銀行|信託兼営"),
    ("bank",          u"銀行業|銀行法|預金(又は|または)?(定期預金|定期積金)?の受入"),
    ("trust_bank",    u"信託業"),
    ("insurer",       u"保険業|生命保険|損害保険|保険会社"),
    # A vehicle whose stated business IS holding securities: a fund SPV, or a
    # founder's 資産管理会社. Economically an owner, not a manager of other
    # people's money — which is why it is not folded into asset_manager.
    ("investment_vehicle", u"投資事業|有価証券の保有|有価証券の運用|有価証券の投資|"
                           u"資産管理|資産の運用|投資業|持株会社|ファンド|"
                           u"有価証券の取得|投資活動|^投資$|株式又は持分を所有"),
)

# Selling insurance as an agent is a trade, not underwriting: テンポス and 小千谷
# 産業 list 損害保険代理業 among a dozen businesses and were read as insurers.
INSURANCE_AGENCY = re.compile(u"(損害|生命)?保険(の)?(代理(業|店)?|募集)(に関する業務)?")

# A company whose filed business is a long numbered list of trades is an
# operating company, even when one line of the list is a financial licence:
# ａｂｃ株式会社 lists seventeen businesses, real estate first, and its eighth
# (金融商品取引業) made it a broker-dealer. Such a list is read by its FIRST
# item. Ten items, because a bank's own list runs to six.
LONG_LIST = 10
LIST_ITEM = re.compile(u"(?:^|\\s|[^0-9０-９])([0-9０-９]{1,2})\\s*[．.、，,)）]")
TYPE_EN = {
    "individual": "Individual",
    "asset_manager": "Asset manager",
    "broker_dealer": "Broker-dealer",
    "trust_bank": "Trust bank",
    "bank": "Bank",
    "insurer": "Insurer",
    "investment_vehicle": "Investment vehicle",
    "operating_company": "Operating company",
    "not_stated": "Not stated",
}
TYPE_NOTE = (
    "filer_type is read from the filer's own 事業内容 (business description) and "
    "the filed 法人/個人 flag — it is derived, not a filed category, and no "
    "filing states a category such as 'hedge fund'. A holder that states no "
    "business is not_stated and renders as a gap; a holder whose stated "
    "business is an ordinary trade is an operating company, which on a 5% "
    "filing means a strategic holder.")


def _first_item(text):
    u"""The first business of a long numbered list, or None for a short one."""
    marks = [m for m in LIST_ITEM.finditer(text)]
    if len(marks) < LONG_LIST:
        return None
    end = marks[1].start() if len(marks) > 1 else len(text)
    return text[marks[0].end():end]


def type_of(business_ja, is_individual=None, holder_type_ja=None, name_ja=None):
    u"""(type_key, evidence) for one holder, from what the filing states.

    The one reading taken from the name is 信託銀行: only a licensed trust
    bank may use it in its legal name, so it is a filed fact, not a guess."""
    if is_individual or (holder_type_ja and u"個人" in holder_type_ja):
        return "individual", u"filed 法人/個人"
    if name_ja and u"信託銀行" in name_ja:
        return "trust_bank", u"legal name"
    filed = (business_ja or "").strip()
    text = INSURANCE_AGENCY.sub("", filed)
    if filed:
        text = _first_item(text) or text
        for key, pattern in TYPE_RULES:
            if re.search(pattern, text.strip()):
                return key, u"filed 事業内容"
        return "operating_company", u"filed 事業内容"
    if holder_type_ja:
        return "not_stated", u"法人/個人 only, no 事業内容"
    return "not_stated", u"nothing filed"


# --- groups -----------------------------------------------------------------
#
# code -> group. Curated from the codes actually present in the archive; the
# comment beside each block is the name the filings use. Only families whose
# membership is a matter of public record are listed, and a joint venture is
# its own group rather than being assigned to one parent.
GROUPS = {}


def _family(name, codes):
    for code in codes:
        GROUPS[code] = name


_family("BlackRock", [
    "E08473", "E08498", "E08507", "E09096", "E20034", "E20311", "E20316",
    "E20318", "E20327", "E20330", "E20332", "E20335", "E20342", "E24803",
    "E26295", "E39679"])
_family("Fidelity", [
    "E12208", "E12481", "E41411", "E41558", "E41559", "E41561", "E41609",
    "E41680", "E41682", "E41686", "E41687", "E41705", "E41706"])
_family("Nomura", [
    "E03752", "E03810", "E06485", "E20003", "E20180", "E20269", "E24333",
    "E41105"])
_family("Mizuho", [
    "E03532", "E03615", "E03628", "E03759", "E08438", "E11329", "E11330"])
# 三菱UFJモルガン・スタンレー証券 (E24321) and Morgan Stanley MUFG Securities
# (E10802) are the two sides of one joint venture and belong to neither parent
# alone, so they are their own group rather than silently counted in both.
_family("Mitsubishi UFJ", ["E03533", "E03626", "E03817", "E05881", "E11518"])
_family("Mitsubishi UFJ Morgan Stanley (JV)", ["E10802", "E24321"])
_family("Morgan Stanley", ["E20016", "E20037", "E30665"])
_family("Sumitomo Mitsui", [
    "E03617", "E03627", "E06693", "E08957", "E12444", "E23615", "E24521"])
_family("J.P. Morgan", [
    "E06135", "E06264", "E09862", "E11311", "E20018", "E20021", "E20036",
    "E21418", "E32776", "E34755"])
_family("Goldman Sachs", ["E05875", "E11198", "E20077", "E20082"])
_family("Wellington Management", [
    "E31250", "E31252", "E31253", "E31254", "E31294", "E36385"])
_family("Capital Group", ["E06267", "E11749", "E11750", "E14703"])
_family("Daiwa", ["E06228", "E06748", "E31720"])
_family("SBI", [
    "E03530", "E03816", "E05159", "E10227", "E12441", "E13447", "E32959",
    "E35850", "E39605"])
_family("Asset Management One", ["E10677", "E20272"])
# Asset Value Investors manages Nippon Active Value Fund and its master fund;
# the three file separately and act as one campaign.
_family("Asset Value Investors", ["E34595", "E36104", "E36950"])
_family("Old Peak", ["E34245", "E40787"])
_family("ValueAct", ["E34044", "E34138"])
# The listed operating company, its securities-holding arm and its Singapore
# investment vehicle — one group, three very different filed businesses.
_family("Hikari Tsushin", ["E04948", "E35239", "E39496"])
# Families whose entities file JOINTLY — named together as joint holders in
# the same reports (counts from the archive, October 2026).
_family("Dalton Investments", ["E39237", "E08827"])                    # 19 joint
_family("Taiyo Pacific", ["E37410", "E30581", "E09183", "E30463",      # 37 joint
                          "E37411", "E36328", "E32623"])
_family("Hibiki Path Advisors", ["E34566", "E41402", "E41592"])        # 12 joint
_family("Alphaleo", ["E34691", "E27283"])                              # 24 joint
_family("Asia Development Capital", ["E04298", "E36808"])              # 12 joint
# One entity under two keys: filed with its EDINET code in some reports and
# with none in others, or under a chair's name that changed. The uncoded key
# is the holder_key /holders lists (name_key, the filed name folded).
_family("Symphony Financial Partners", [
    "E30522",
    u"シンフォニー・フィナンシャル・パートナーズ(シンガポール)ピーティーイー・リミテッド"
    u"(SymphonyFinancialPartners(Singapore)Pte.Ltd.)"])
_family("Be Brave", ["E37710", u"BeBrave株式会社代表取締役泉田和人"])
# GFA株式会社 renamed itself ａｂｃ株式会社 and kept its code, E03740.
_family("abc (formerly GFA)", ["E03740", u"GFA株式会社"])
_family("Kimoto Kyoeikai", ["E33823", "E38941"])
# Five members of one family who file separately, on the same issuer (北王
# ＧＲＯＵＰ), on the same day, with the same purpose text. Not joint holders on the
# form, but one holding in substance.
_family("Furuse family", ["E41798", "E41799", "E41800", "E41801", "E41802"])

GROUP_NOTE = (
    "Filing entities are consolidated into their group where the group is a "
    "matter of public record — BlackRock files under sixteen EDINET codes, "
    "Fidelity thirteen, Nomura eight, and no filing names the parent, so this "
    "map is curated rather than derived. An entity not in the map is its own "
    "group. A joint venture is its own group and is never counted inside "
    "either parent.")


def group_of(code, fallback=None):
    u"""The group a filing entity belongs to, or its own name. `code` is the
    EDINET code, or the holder_key of a holder that has none."""
    return GROUPS.get(code) or fallback


def group_size(name):
    u"""How many filing entities the map puts in this group."""
    return sum(1 for v in GROUPS.values() if v == name)
