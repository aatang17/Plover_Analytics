"""What kind of network a visit came from, and whether it names an organisation.

The visit log keeps the operator of the network a request came from (DB-IP's
name for it), never the address. For a reader at their desk that operator is
often their employer: a fund, a bank, a university. For a reader on a phone or
at home it is their internet provider, which says nothing about who they are.
This module sorts the two apart, so the Traffic page can list the
organisations and leave the providers out.

The sort is read off the operator's name, so it is an estimate. Only a name
that says what it is (a university, a bank, a fund, or a firm named in
KNOWN_FIRMS) is claimed as an organisation; a name that says neither is shown
as it is and claimed as nothing. It is decided when the log is read, never stored, so a
better list next month re-sorts every past visit too.
"""
import functools
import re
import unicodedata

# The kinds, as the admin page names them.
ORGANISATION = "organisation"
SECURITY = "security"      # a company's web security service: the company is hidden
PROVIDER = "provider"      # home broadband or a mobile network
HOSTING = "hosting"        # a data centre: a machine, or a VPN
OTHER = "other"            # a name that says neither: shown, never claimed
UNKNOWN = "unknown"        # no operator recorded

# Whole words that make a name an organisation. Only these are claimed: a
# provider's name without a provider word (hundreds of small carriers) must
# not be passed off as somebody's employer.
ORG_WORDS = (r"universit\w*", r"college", r"school", r"academ\w*", r"institut\w*",
             r"banks?", r"banco", r"bancorp", r"capital", r"securities", r"assets?",
             r"investments?", r"investors?", r"management", r"partners", r"funds?",
             r"advisors", r"advisers", r"wealth", r"brokerage", r"equities",
             r"insurance", r"assurance", r"research", r"ministry", r"government",
             r"council", r"agency", r"authority", r"parliament", r"embassy",
             r"consulting", r"llp", r"hospital", r"laborator\w*", r"foundation",
             r"museum", r"library", r"federal reserve", r"monetary")

# Firms whose network name carries none of those words.
KNOWN_FIRMS = (r"goldman sachs", r"morgan stanley", r"jpmorgan", r"j\.p\. morgan",
               r"citigroup", r"citibank", r"citadel", r"blackrock", r"vanguard",
               r"fidelity", r"nomura", r"mizuho", r"mufg", r"mitsubishi ufj",
               r"sumitomo mitsui", r"smbc", r"daiwa", r"ubs", r"credit suisse",
               r"barclays", r"hsbc", r"bnp paribas", r"societe generale",
               r"state street", r"invesco", r"schroders", r"pimco", r"bridgewater",
               r"two sigma", r"millennium", r"point72", r"d\. e\. shaw", r"jane street",
               r"bloomberg", r"refinitiv", r"lseg", r"factset", r"s&p global",
               r"moody'?s", r"msci", r"thomson reuters", r"nikkei", r"financial times",
               r"dow jones", r"imf", r"oecd", r"ecb", r"macquarie", r"standard chartered",
               r"dbs", r"temasek", r"gic", r"nippon life", r"dai-ichi life", r"orix",
               r"sbi holdings", r"monex", r"rakuten securities", r"jefferies",
               r"bank of america", r"wells fargo", r"charles schwab", r"t\. rowe price",
               r"franklin templeton", r"amundi", r"allianz", r"axa", r"prudential",
               r"manulife", r"aia group", r"ping an", r"cicc", r"citic", r"mckinsey",
               r"boston consulting", r"bain & company", r"deloitte", r"pwc",
               r"pricewaterhouse", r"ernst & young", r"kpmg", r"accenture")

# Corporate web security services. Staff of a company that uses one reach the
# web through it, so the visit shows the service's name, never the company's.
SECURITY_WORDS = (r"zscaler", r"netskope", r"forcepoint", r"menlo security", r"iboss",
                  r"cisco umbrella", r"opendns", r"palo alto networks", r"prisma access",
                  r"skyhigh security", r"cato networks", r"versa networks")

# Internet providers: home broadband, mobile, national carriers. Matched
# anywhere in the name, so "Telecomunicacoes" and "Telekom" both count.
PROVIDER_WORDS = (
    "telecom", "telekom", "telecomunica", "telecommunica", "telefon", "telco",
    "communication", "comunica", "mobile", "mobil", "broadband", "banda ancha",
    "cable", "internet", "wireless", "fiber", "fibra", "fibre", "network", " net ",
    "telephone", "cellular", "celular", "satelit", "satellit", "tv ", "television",
    "softbank", "kddi", "ntt ", "docomo", "rakuten mobile", "jcom", "j:com",
    "sony network", "so-net", "biglobe", "optage", "comcast", "verizon", "at&t",
    "charter", "cox ", "spectrum", "t-mobile", "centurylink", "lumen", "frontier",
    "windstream", "altice", "optimum", "mediacom", "bell canada", "rogers", "telus",
    "shaw", "vodafone", "orange", "virgin", "hutchison", "free sas", "free pro",
    "sfr", "bouygues", "proximus", "kpn", "ziggo", "swisscom", "telia", "telenor",
    "rostelecom", "vimpelcom", "kyivstar", "etisalat", "ooredoo", "safaricom",
    "vodacom", "rain group", "airtel", "jio", "reliance", "pldt", "telstra",
    "optus", "chunghwa", "hinet", "china mobile", "china unicom", "chinanet",
    "pccw", "hkt", "hkbn", "csl limited", "smartone", "singtel", "starhub",
    "myrepublic", "viettel", "vnpt", "mobifone", "maxis", "celcom", "indosat",
    "telkomsel", "claro", "telmex", "tigo", "movistar", "entel", "uninet", "v tal",
    "digicel", "space exploration", "starlink", "fiberpower", "eircom", "hyperoptic",
    "eastlink", "xplore", "grameenphone", "axiata", "telecel", "telgua", "cantv",
    "wom s.a", "vero s.a", "flow", "ucom", "silknet", "lifecell", "ufanet")

# Data centres: visits.HOSTING_MARKERS is the list the reader test uses, and
# this module defers to it rather than keeping a second copy.

# Names that read badly as DB-IP gives them.
FRIENDLY = {
    "research organization of information and systems, national institute of informa":
        "SINET (Japanese universities and research institutes)",
    "research organization of information and systems, national institute of informatics":
        "SINET (Japanese universities and research institutes)",
}

_SPACES = re.compile(r"\s+")


def _words(words):
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(words) + r")(?![a-z0-9])")


_ORG = _words(ORG_WORDS)
_FIRM = _words(KNOWN_FIRMS)
_SECURITY = _words(SECURITY_WORDS)


def _plain(network):
    """Lower case, accents off, single spaces: "TELEFÔNICA" -> "telefonica"."""
    folded = unicodedata.normalize("NFKD", network.strip().lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _SPACES.sub(" ", folded)


@functools.lru_cache(maxsize=8192)
def kind(network):
    """One of ORGANISATION, SECURITY, PROVIDER, HOSTING, OTHER or UNKNOWN."""
    from .visits import is_google_machine, is_hosting
    if not network or not network.strip():
        return UNKNOWN
    name = _plain(network)
    if _SECURITY.search(name):
        return SECURITY
    if is_hosting(network) or is_google_machine(network):
        return HOSTING
    padded = " " + name + " "
    if any(word in padded for word in PROVIDER_WORDS):
        return PROVIDER
    if _FIRM.search(name) or _ORG.search(name):
        return ORGANISATION
    return OTHER


def label(network):
    """The operator's name as the admin page shows it."""
    if not network or not network.strip():
        return None
    return FRIENDLY.get(_SPACES.sub(" ", network.strip().lower()), network.strip())


def organisation(network):
    """The organisation's name when the network names one, else None."""
    return label(network) if kind(network) == ORGANISATION else None
