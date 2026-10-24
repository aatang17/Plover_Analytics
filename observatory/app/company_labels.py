# -*- coding: utf-8 -*-
u"""The hand-edited company list: English names, alternative spellings, groups
and themes. The issuer-side counterpart to `filer_labels.py`.

Why a curated file at all
-------------------------
Two problems the filings cannot solve on their own, both the same shape as the
one `filer_labels.py` solves for 5% filers:

  * **A buyer is written differently by every supplier that names it.** Toyota
    appears as 「トヨタ自動車(株)」 and 「トヨタ自動車株式会社」, Honda three ways,
    Denso three ways — 70 buyers in the current filings are written more than
    one way. No filing states which spellings are the same company.
  * **Half of them have no English name anywhere in the machine-readable
    record.** Government bodies, unlisted subsidiaries and foreign affiliates
    are not in the EDINET company registry, so nothing can look them up.

Everything here is therefore DERIVED and applied at serve time. The extractors
store only what the filing says; changing an entry costs a redeploy, never a
re-extraction, and never rewrites a vintage.

The rules this file obeys
-------------------------
1. **The as-filed name is never replaced.** Every surface shows it. An English
   name and a group are labels attached to it, and both say where they came
   from. Two spellings of one buyer stay two rows unless a reader asks to
   group them.
2. **Nothing is translated.** An English name is the organisation's own
   published one, or it is absent. A machine translation on an institutional
   product is worse than a Japanese name, because the reader cannot tell which
   is which.
3. **A subsidiary keeps its own identity.** Amazon Japan G.K. is not
   Amazon.com; TEPCO Energy Partner is not TEPCO; Toyota Boshoku is not Toyota
   Motor. Rolling a subsidiary into its parent would state a relationship no
   filer disclosed. `group` records the family separately, for readers who
   want it, and is never applied to the name.
4. **A group is a matter of public record or it is absent.** Never guessed
   from a shared word: Sumitomo Corporation and Sumitomo Mitsui Banking are
   not one company, and Nissan Chemical has nothing to do with Nissan Motor.
5. **Aliases are only spellings actually seen in filings.** Each is copied
   from the archive, so any entry can be checked against a document. Adding a
   plausible-looking spelling nobody wrote would make the alias list a guess.

How to edit it
--------------
Add or change an entry in `COMPANIES` and redeploy — no re-extraction. The
key is a short stable slug of your choosing; nothing else refers to it. Run
`python -m app.company_labels --check` to see which aliases no longer appear
in the filings and which filed names still have no English name.
"""
import json
import os
import re
import unicodedata

# --- the list ---------------------------------------------------------------
#
# slug: {
#   "name_en":  the organisation's own published English name, or None
#   "name_ja":  its own Japanese name (the canonical one, not a filer's spelling)
#   "sec_code": securities code if listed in Japan, else None
#   "aliases":  spellings SEEN IN FILINGS, verbatim, including punctuation
#   "group":    corporate family, or None. Public record only.
#   "tags":     themes this company belongs to, for screens and mappings
# }
#
# tags in use: "memory", "semiconductor", "semicap" (chipmaking equipment),
# "wafer", "materials", "government", "distributor", "trading", "telecom".

COMPANIES = {

    # --- semiconductors: memory ---------------------------------------------
    "kioxia": {
        "name_en": "Kioxia Holdings Corporation", "name_ja": u"キオクシアホールディングス株式会社",
        "sec_code": "285A", "group": None, "tags": ["memory", "semiconductor"],
        "aliases": [u"キオクシア株式会社"]},
    "samsung_electronics": {
        "name_en": "Samsung Electronics Co., Ltd.", "name_ja": None,
        "sec_code": None, "group": "Samsung", "tags": ["memory", "semiconductor"],
        "aliases": [u"Samsung Electronics Co., Ltd.", u"サムスングループ",
                    u"SAMSUNG AUSTIN SEMICONDUCTOR,L.L.C."]},
    "samsung_display": {
        "name_en": "Samsung Display Co., Ltd.", "name_ja": None,
        "sec_code": None, "group": "Samsung", "tags": ["semiconductor"],
        "aliases": [u"Samsung Display Co., LTD"]},
    "sk_hynix": {
        "name_en": "SK hynix Inc.", "name_ja": None, "sec_code": None,
        "group": "SK", "tags": ["memory", "semiconductor"],
        "aliases": [u"SK Hynix Inc."]},
    "micron_taiwan": {
        "name_en": "Micron Memory Taiwan Co., Ltd.", "name_ja": None,
        "sec_code": None, "group": "Micron", "tags": ["memory", "semiconductor"],
        "aliases": [u"MICRON MEMORY TAIWAN Co.,Ltd."]},
    "western_digital": {
        "name_en": "Western Digital Storage Technologies", "name_ja": None,
        "sec_code": None, "group": "Western Digital", "tags": ["memory"],
        "aliases": [u"WESTERN DIGITAL STORAGE TECHNOLOGIES(JAPAN)"]},

    # --- semiconductors: foundry, logic, equipment ---------------------------
    "tsmc": {
        "name_en": "Taiwan Semiconductor Manufacturing Company Ltd.", "name_ja": None,
        "sec_code": None, "group": None, "tags": ["semiconductor"],
        "aliases": [u"Taiwan Semiconductor Manufacturing Company Ltd.",
                    u"Taiwan Semiconductor Manufacturing Company, Ltd.",
                    u"Taiwan Semiconductor Manufacturing Co., Ltd."]},
    "tokyo_electron": {
        "name_en": "Tokyo Electron Limited", "name_ja": u"東京エレクトロン株式会社",
        "sec_code": "8035", "group": None, "tags": ["semicap"], "aliases": []},
    "tokyo_electron_miyagi": {
        "name_en": "Tokyo Electron Miyagi Limited", "name_ja": u"東京エレクトロン宮城株式会社",
        "sec_code": None, "group": "Tokyo Electron", "tags": ["semicap"],
        "aliases": [u"東京エレクトロン宮城(株)"]},
    "advantest": {
        "name_en": "ADVANTEST CORPORATION", "name_ja": u"株式会社アドバンテスト",
        "sec_code": "6857", "group": None, "tags": ["semicap"], "aliases": []},
    "screen": {
        "name_en": "SCREEN Holdings Co., Ltd.", "name_ja": u"株式会社SCREENホールディングス",
        "sec_code": "7735", "group": None, "tags": ["semicap"], "aliases": []},
    "disco": {
        "name_en": "DISCO CORPORATION", "name_ja": u"株式会社ディスコ",
        "sec_code": "6146", "group": None, "tags": ["semicap"], "aliases": []},
    "kokusai": {
        "name_en": "KOKUSAI ELECTRIC CORPORATION", "name_ja": u"株式会社KOKUSAI ELECTRIC",
        "sec_code": "6525", "group": None, "tags": ["semicap"], "aliases": []},
    "lasertec": {
        "name_en": "Lasertec Corporation", "name_ja": u"レーザーテック株式会社",
        "sec_code": "6920", "group": None, "tags": ["semicap"], "aliases": []},
    "tokyo_seimitsu": {
        "name_en": "TOKYO SEIMITSU CO., LTD.", "name_ja": u"株式会社東京精密",
        "sec_code": "7729", "group": None, "tags": ["semicap"], "aliases": []},
    "renesas": {
        "name_en": "Renesas Electronics Corporation", "name_ja": u"ルネサスエレクトロニクス株式会社",
        "sec_code": "6723", "group": None, "tags": ["semiconductor"], "aliases": []},
    "rohm": {
        "name_en": "ROHM CO., LTD.", "name_ja": u"ローム株式会社",
        "sec_code": "6963", "group": None, "tags": ["semiconductor"], "aliases": []},
    "socionext": {
        "name_en": "Socionext Inc.", "name_ja": u"株式会社ソシオネクスト",
        "sec_code": "6526", "group": None, "tags": ["semiconductor"], "aliases": []},

    # --- semiconductors: wafers and materials --------------------------------
    "shin_etsu": {
        "name_en": "Shin-Etsu Chemical Co., Ltd.", "name_ja": u"信越化学工業株式会社",
        "sec_code": "4063", "group": None, "tags": ["wafer", "materials"], "aliases": []},
    "sumco": {
        "name_en": "SUMCO CORPORATION", "name_ja": u"株式会社SUMCO",
        "sec_code": "3436", "group": None, "tags": ["wafer", "materials"], "aliases": []},
    "tokyo_ohka": {
        "name_en": "TOKYO OHKA KOGYO CO., LTD.", "name_ja": u"東京応化工業株式会社",
        "sec_code": "4186", "group": None, "tags": ["materials"], "aliases": []},
    "resonac": {
        "name_en": "Resonac Holdings Corporation", "name_ja": u"株式会社レゾナック・ホールディングス",
        "sec_code": "4004", "group": None, "tags": ["materials"], "aliases": []},
    "kanto_denka": {
        "name_en": "KANTO DENKA KOGYO CO., LTD.", "name_ja": u"関東電化工業株式会社",
        "sec_code": "4047", "group": None, "tags": ["materials"], "aliases": []},
    "nomura_micro": {
        "name_en": "Nomura Micro Science Co., Ltd.", "name_ja": u"野村マイクロ・サイエンス株式会社",
        "sec_code": "6254", "group": None, "tags": ["semicap"], "aliases": []},
    "japan_material": {
        "name_en": "JAPAN MATERIAL Co., Ltd.", "name_ja": u"株式会社ジャパンマテリアル",
        "sec_code": "6055", "group": None, "tags": ["semicap"], "aliases": []},
    "jem": {
        "name_en": "JAPAN ELECTRONIC MATERIALS CORPORATION", "name_ja": u"日本電子材料株式会社",
        "sec_code": "6855", "group": None, "tags": ["materials"], "aliases": []},

    # --- big Japanese buyers, written several ways ---------------------------
    "toyota_motor": {
        "name_en": "TOYOTA MOTOR CORPORATION", "name_ja": u"トヨタ自動車株式会社",
        "sec_code": "7203", "group": "Toyota", "tags": [],
        "aliases": [u"トヨタ自動車(株)", u"トヨタ自動車株式会社"]},
    "denso": {
        "name_en": "DENSO CORPORATION", "name_ja": u"株式会社デンソー",
        "sec_code": "6902", "group": "Toyota", "tags": [],
        "aliases": [u"株式会社デンソー", u"(株)デンソー", u"(株)デンソー(グループ会社含む)"]},
    "toyota_boshoku": {
        "name_en": "TOYOTA BOSHOKU CORPORATION", "name_ja": u"トヨタ紡織株式会社",
        "sec_code": "3116", "group": "Toyota", "tags": [], "aliases": []},
    "honda": {
        "name_en": "HONDA MOTOR CO., LTD.", "name_ja": u"本田技研工業株式会社",
        "sec_code": "7267", "group": None, "tags": [],
        "aliases": [u"本田技研工業株式会社", u"本田技研工業(株)", u"本田技研工業"]},
    "nissan": {
        "name_en": "NISSAN MOTOR CO., LTD.", "name_ja": u"日産自動車株式会社",
        "sec_code": "7201", "group": "Nissan", "tags": [],
        "aliases": [u"日産自動車株式会社", u"日産自動車(株)", u"日産自動車株式会社グループ"]},
    "nissan_mexicana": {
        "name_en": "Nissan Mexicana, S.A. de C.V.", "name_ja": None,
        "sec_code": None, "group": "Nissan", "tags": [],
        "aliases": [u"メキシコ日産自動車会社"]},
    "nissan_north_america": {
        "name_en": "Nissan North America, Inc.", "name_ja": None,
        "sec_code": None, "group": "Nissan", "tags": [],
        "aliases": [u"北米日産会社"]},
    "dentsu": {
        "name_en": "DENTSU GROUP INC.", "name_ja": u"株式会社電通グループ",
        "sec_code": "4324", "group": None, "tags": [],
        "aliases": [u"(株)電通", u"株式会社電通"]},
    "hakuhodo": {
        "name_en": "Hakuhodo Inc.", "name_ja": u"株式会社博報堂",
        "sec_code": None, "group": "Hakuhodo DY", "tags": [],
        "aliases": [u"(株)博報堂", u"株式会社博報堂"]},
    "nippon_steel": {
        "name_en": "NIPPON STEEL CORPORATION", "name_ja": u"日本製鉄株式会社",
        "sec_code": "5401", "group": None, "tags": [],
        "aliases": [u"日本製鉄(株)", u"日本製鉄株式会社"]},
    "fujitsu": {
        "name_en": "FUJITSU LIMITED", "name_ja": u"富士通株式会社",
        "sec_code": "6702", "group": None, "tags": [],
        "aliases": [u"富士通株式会社", u"富士通(株)"]},
    "nintendo": {
        "name_en": "Nintendo Co., Ltd.", "name_ja": u"任天堂株式会社",
        "sec_code": "7974", "group": None, "tags": [],
        "aliases": [u"任天堂(株)", u"任天堂株式会社"]},
    "mitsui": {
        "name_en": "MITSUI & CO., LTD.", "name_ja": u"三井物産株式会社",
        "sec_code": "8031", "group": None, "tags": ["trading"],
        "aliases": [u"三井物産(株)", u"三井物産株式会社"]},
    "suzuken": {
        "name_en": "SUZUKEN CO., LTD.", "name_ja": u"株式会社スズケン",
        "sec_code": "9987", "group": None, "tags": ["distributor"],
        "aliases": [u"(株)スズケン", u"株式会社スズケン"]},
    "mitsubishi_shokuhin": {
        "name_en": "Mitsubishi Shokuhin Co., Ltd.", "name_ja": u"三菱食品株式会社",
        "sec_code": "7451", "group": "Mitsubishi", "tags": ["distributor"],
        "aliases": [u"三菱食品株式会社", u"三菱食品(株)"]},
    "mitsubishi_rtm": {
        "name_en": "Mitsubishi Corporation RtM Japan Ltd.", "name_ja": None,
        "sec_code": None, "group": "Mitsubishi", "tags": ["trading"],
        "aliases": [u"三菱商事RtMジャパン株式会社"]},
    "mediceo": {
        "name_en": "Mediceo Corporation", "name_ja": u"株式会社メディセオ",
        "sec_code": None, "group": "Medipal", "tags": ["distributor"],
        "aliases": [u"(株)メディセオ", u"株式会社メディセオ"]},
    "toho_pharmaceutical": {
        "name_en": "Toho Pharmaceutical Co., Ltd.", "name_ja": u"東邦薬品株式会社",
        "sec_code": None, "group": "Toho Holdings", "tags": ["distributor"],
        "aliases": [u"東邦薬品(株)", u"東邦薬品株式会社"]},
    "ohki": {
        "name_en": "Ohki Healthcare Holdings Co., Ltd.", "name_ja": u"株式会社大木ヘルスケアホールディングス",
        "sec_code": None, "group": None, "tags": ["distributor"], "aliases": [u"(株)大木"]},
    "seven_eleven_japan": {
        "name_en": "Seven-Eleven Japan Co., Ltd.", "name_ja": u"株式会社セブン-イレブン・ジャパン",
        "sec_code": None, "group": "Seven & i", "tags": [],
        "aliases": [u"(株)セブン-イレブン・ジャパン", u"株式会社セブン-イレブン・ジャパン"]},
    "tbs_television": {
        "name_en": "TBS Television, Inc.", "name_ja": u"株式会社TBSテレビ",
        "sec_code": None, "group": "TBS", "tags": [], "aliases": [u"(株)TBSテレビ"]},
    "ana": {
        "name_en": "All Nippon Airways Co., Ltd.", "name_ja": u"全日本空輸株式会社",
        "sec_code": None, "group": "ANA", "tags": [], "aliases": [u"全日本空輸株式会社"]},
    "itochu_kenzai": {
        "name_en": "Itochu Kenzai Corporation", "name_ja": u"伊藤忠建材株式会社",
        "sec_code": None, "group": "Itochu", "tags": ["trading"], "aliases": [u"伊藤忠建材(株)"]},
    "smb_kenzai": {
        "name_en": "SMB Kenzai Co., Ltd.", "name_ja": u"SMB建材株式会社",
        "sec_code": None, "group": None, "tags": ["trading"], "aliases": [u"SMB建材(株)"]},
    "hayashi_telempu": {
        "name_en": "Hayashi Telempu Corporation", "name_ja": u"林テレンプ株式会社",
        "sec_code": None, "group": None, "tags": [], "aliases": [u"林テレンプ株式会社"]},
    "confex": {
        "name_en": "Confex Co., Ltd.", "name_ja": u"コンフェックス株式会社",
        "sec_code": None, "group": None, "tags": ["distributor"], "aliases": [u"コンフェックス株式会社"]},

    # Wholesalers and trading companies that appear as a named customer. Every
    # one of these buys to resell: the supplier's dependence on the name is a
    # route-to-market fact, not exposure to that buyer's own end demand. The
    # Customers screen filters on this tag, so an entry here is a platform
    # classification and the screen says so.
    "nippon_access": {
        "name_en": "Nippon Access, Inc.", "name_ja": u"株式会社日本アクセス",
        "sec_code": None, "group": "Itochu", "tags": ["distributor"],
        "aliases": [u"(株)日本アクセス", u"株式会社日本アクセス"]},
    "kato_sangyo": {
        "name_en": "KATO SANGYO CO.,LTD.", "name_ja": u"加藤産業株式会社",
        "sec_code": "9869", "group": None, "tags": ["distributor"],
        "aliases": [u"加藤産業(株)", u"加藤産業株式会社"]},
    "kokubu": {
        "name_en": None, "name_ja": u"国分グループ本社株式会社",
        "sec_code": None, "group": None, "tags": ["distributor"],
        "aliases": [u"国分グループ本社株式会社", u"国分グループ本社(株)"]},
    "paltac": {
        "name_en": None, "name_ja": u"株式会社PALTAC",
        "sec_code": None, "group": None, "tags": ["distributor"], "aliases": [u"(株)PALTAC"]},
    "arata": {
        "name_en": "ARATA CORPORATION", "name_ja": u"株式会社あらた",
        "sec_code": "2733", "group": None, "tags": ["distributor"], "aliases": [u"(株)あらた"]},
    "alfresa": {
        "name_en": None, "name_ja": u"アルフレッサ株式会社",
        "sec_code": None, "group": "Alfresa", "tags": ["distributor"], "aliases": [u"アルフレッサ(株)"]},
    "alfresa_holdings": {
        "name_en": "Alfresa Holdings Corporation", "name_ja": u"アルフレッサホールディングス株式会社",
        "sec_code": "2784", "group": "Alfresa", "tags": ["distributor"],
        "aliases": [u"アルフレッサホールディングス株式会社",
                    u"アルフレッサホールディングス(株)"]},
    "alfresa_healthcare": {
        "name_en": None, "name_ja": u"アルフレッサヘルスケア株式会社",
        "sec_code": None, "group": "Alfresa", "tags": ["distributor"],
        "aliases": [u"アルフレッサヘルスケア(株)"]},
    "medipal": {
        "name_en": "MEDIPAL HOLDINGS CORPORATION", "name_ja": u"株式会社メディパルホールディングス",
        "sec_code": "7459", "group": "Medipal", "tags": ["distributor"],
        "aliases": [u"(株)メディパルホールディングス",
                    u"株式会社メディパルホールディングス",
                    u"株式会社メディパルホールディングス (注)"]},
    "restar": {
        "name_en": "Restar Corporation", "name_ja": u"株式会社レスター",
        "sec_code": "3156", "group": "Restar", "tags": ["distributor"],
        "aliases": [u"株式会社レスター", u"株式会社レスターおよびその子会社"]},
    "macnica": {
        "name_en": "MACNICA, Inc.", "name_ja": u"株式会社マクニカ",
        "sec_code": None, "group": "Macnica", "tags": ["distributor"], "aliases": [u"株式会社マクニカ"]},
    "mckesson": {
        "name_en": "McKesson Corporation", "name_ja": None,
        "sec_code": None, "group": None, "tags": ["distributor"], "aliases": [u"McKesson Corporation"]},
    "cencora": {
        "name_en": "Cencora, Inc.", "name_ja": None,
        "sec_code": None, "group": None, "tags": ["distributor"], "aliases": [u"Cencora,Inc."]},
    "hanwa": {
        "name_en": "HANWA CO.,LTD.", "name_ja": u"阪和興業株式会社",
        "sec_code": "8078", "group": None, "tags": ["trading"],
        "aliases": [u"阪和興業株式会社", u"阪和興業(株)"]},
    "sumitomo_corp": {
        "name_en": "SUMITOMO CORPORATION", "name_ja": u"住友商事株式会社",
        "sec_code": "8053", "group": "Sumitomo", "tags": ["trading"], "aliases": [u"住友商事株式会社"]},
    "itochu": {
        "name_en": "ITOCHU Corporation", "name_ja": u"伊藤忠商事株式会社",
        "sec_code": "8001", "group": "Itochu", "tags": ["trading"],
        "aliases": [u"伊藤忠商事(株)", u"伊藤忠商事株式会社"]},
    "mitsubishi_corp": {
        "name_en": "Mitsubishi Corporation", "name_ja": u"三菱商事株式会社",
        "sec_code": "8058", "group": "Mitsubishi", "tags": ["trading"],
        "aliases": [u"三菱商事(株)", u"三菱商事株式会社"]},
    "marubeni": {
        "name_en": "Marubeni Corporation", "name_ja": u"丸紅株式会社",
        "sec_code": "8002", "group": "Marubeni", "tags": ["trading"],
        "aliases": [u"丸紅(株)", u"丸紅株式会社"]},
    "sojitz": {
        "name_en": "Sojitz Corporation", "name_ja": u"双日株式会社",
        "sec_code": "2768", "group": "Sojitz", "tags": ["trading"], "aliases": [u"双日株式会社"]},
    "toyota_tsusho": {
        "name_en": "TOYOTA TSUSHO CORPORATION", "name_ja": u"豊田通商株式会社",
        "sec_code": "8015", "group": "Toyota", "tags": ["trading"], "aliases": [u"豊田通商株式会社"]},
    "kanematsu": {
        "name_en": "KANEMATSU CORPORATION", "name_ja": u"兼松株式会社",
        "sec_code": "8020", "group": "Kanematsu", "tags": ["trading"], "aliases": [u"兼松株式会社"]},
    "inabata": {
        "name_en": "Inabata&Co.,Ltd.", "name_ja": u"稲畑産業株式会社",
        "sec_code": "8098", "group": None, "tags": ["trading"], "aliases": [u"稲畑産業株式会社"]},
    "shinsho": {
        "name_en": "Shinsho Corporation", "name_ja": u"神鋼商事株式会社",
        "sec_code": "8075", "group": "Kobe Steel", "tags": ["trading"], "aliases": [u"神鋼商事(株)"]},
    "nagase": {
        "name_en": "NAGASE & CO., LTD.", "name_ja": u"長瀬産業株式会社",
        "sec_code": "8012", "group": None, "tags": ["trading"],
        "aliases": [u"長瀬産業株式会社", u"長瀬産業(株)"]},
    "shinsei_pulp": {
        "name_en": "SHINSEI PULP&PAPER COMPANY LIMITED", "name_ja": u"新生紙パルプ商事株式会社",
        "sec_code": None, "group": None, "tags": ["trading"], "aliases": [u"新生紙パルプ商事(株)"]},
    "imm_techno_steel": {
        "name_en": None, "name_ja": u"伊藤忠丸紅住商テクノスチール株式会社",
        "sec_code": None, "group": None, "tags": ["trading"],
        "aliases": [u"伊藤忠丸紅住商テクノスチール株式会社"]},

    # --- telecom and utilities ----------------------------------------------
    "ntt_docomo": {
        "name_en": "NTT DOCOMO, INC.", "name_ja": u"株式会社NTTドコモ",
        "sec_code": None, "group": "NTT", "tags": ["telecom"],
        "aliases": [u"(株)NTTドコモ", u"株式会社NTTドコモ"]},
    "ntt_east": {
        "name_en": "NTT East Corporation", "name_ja": u"東日本電信電話株式会社",
        "sec_code": None, "group": "NTT", "tags": ["telecom"],
        "aliases": [u"NTT東日本(株) (旧東日本電信電話(株))"]},
    "ntt_west": {
        "name_en": "NTT West Corporation", "name_ja": u"西日本電信電話株式会社",
        "sec_code": None, "group": "NTT", "tags": ["telecom"],
        "aliases": [u"NTT西日本(株) (旧西日本電信電話(株))", u"NTT西日本株式会社"]},
    "tepco_ep": {
        "name_en": "TEPCO Energy Partner, Incorporated", "name_ja": u"東京電力エナジーパートナー株式会社",
        "sec_code": None, "group": "TEPCO", "tags": [],
        "aliases": [u"東京電力エナジーパートナー株式会社"]},
    "tepco_pg": {
        "name_en": "TEPCO Power Grid, Incorporated", "name_ja": u"東京電力パワーグリッド株式会社",
        "sec_code": None, "group": "TEPCO", "tags": [],
        "aliases": [u"東京電力パワーグリッド株式会社"]},
    "rikuden_td": {
        "name_en": "Rikuden Power Transmission & Distribution Co., Inc.",
        "name_ja": u"北陸電力送配電株式会社", "sec_code": None, "group": "Hokuriku Electric",
        "tags": [], "aliases": [u"北陸電力送配電(株)"]},

    # --- foreign buyers already written in Latin -----------------------------
    "amazon_japan": {
        "name_en": "Amazon Japan G.K.", "name_ja": u"アマゾンジャパン合同会社",
        "sec_code": None, "group": "Amazon", "tags": [],
        "aliases": [u"アマゾンジャパン合同会社", u"アマゾンジャパン(同)"]},
    "apple": {
        "name_en": "Apple Inc.", "name_ja": None, "sec_code": None,
        "group": None, "tags": [], "aliases": [u"Apple Inc."]},
    "google": {
        "name_en": "Google LLC", "name_ja": None, "sec_code": None,
        "group": "Alphabet", "tags": [], "aliases": [u"Google Inc."]},
    "nvidia": {
        "name_en": "NVIDIA Corporation", "name_ja": None, "sec_code": None,
        "group": None, "tags": ["semiconductor"],
        "aliases": [u"NVIDIAグループ"]},

    # --- government and public bodies ---------------------------------------
    "mlit": {
        "name_en": "Ministry of Land, Infrastructure, Transport and Tourism",
        "name_ja": u"国土交通省", "sec_code": None, "group": "Government of Japan",
        "tags": ["government"], "aliases": [u"国土交通省"]},
    "mod": {
        "name_en": "Ministry of Defense", "name_ja": u"防衛省", "sec_code": None,
        "group": "Government of Japan", "tags": ["government"], "aliases": [u"防衛省"]},
    "atla": {
        "name_en": "Acquisition, Technology and Logistics Agency", "name_ja": u"防衛装備庁",
        "sec_code": None, "group": "Government of Japan", "tags": ["government"],
        "aliases": [u"防衛装備庁"]},
    "mhlw": {
        "name_en": "Ministry of Health, Labour and Welfare", "name_ja": u"厚生労働省",
        "sec_code": None, "group": "Government of Japan", "tags": ["government"],
        "aliases": [u"厚生労働省"]},
    "maff": {
        "name_en": "Ministry of Agriculture, Forestry and Fisheries", "name_ja": u"農林水産省",
        "sec_code": None, "group": "Government of Japan", "tags": ["government"],
        "aliases": [u"農林水産省"]},
    "meti": {
        "name_en": "Ministry of Economy, Trade and Industry", "name_ja": u"経済産業省",
        "sec_code": None, "group": "Government of Japan", "tags": ["government"],
        "aliases": [u"経済産業省"]},
    "tokyo_metro_gov": {
        "name_en": "Tokyo Metropolitan Government", "name_ja": u"東京都",
        "sec_code": None, "group": "Government of Japan", "tags": ["government"],
        "aliases": [u"東京都"]},
    "jehdra": {
        "name_en": "Japan Expressway Holding and Debt Repayment Agency",
        "name_ja": u"独立行政法人日本高速道路保有・債務返済機構", "sec_code": None,
        "group": "Government of Japan", "tags": ["government"],
        "aliases": [u"独立行政法人日本高速道路保有・債務返済機構"]},
    "tokyo_kokuho": {
        "name_en": "Tokyo National Health Insurance Organization",
        "name_ja": u"東京都国民健康保険団体連合会", "sec_code": None,
        "group": "Government of Japan", "tags": ["government"],
        "aliases": [u"東京都国民健康保険団体連合会"]},
}

NOTE = (
    "Company names, corporate families and themes are curated here, not read "
    "from a filing: no document states that two spellings are the same buyer, "
    "and half the buyers named have no English name in the machine-readable "
    "record. The name a filer wrote is always shown and is never replaced; an "
    "English name is the organisation's own published one or is absent, never "
    "a translation. A subsidiary keeps its own identity and is never rolled "
    "into its parent — the family is recorded separately.")


# --- lookup ------------------------------------------------------------------

_SUFFIX_RE = re.compile(
    u"(株式会社|有限会社|合同会社|合資会社|\\(株\\)|（株）|㈱|㈲|\\(有\\)|"
    u"Co\\.?,?\\s*Ltd\\.?|Corporation|Corp\\.?|Inc\\.?|Limited|Ltd\\.?|"
    u"Company|K\\.K\\.|Holdings?|グループ|ホールディングス)", re.I)
_PAREN_RE = re.compile(u"[（(][^）)]*[）)]")
_PUNCT_RE = re.compile(u"[\\s,．.・、'’\"\\-]")


def match_key(name):
    u"""A comparison key. Never a display name, never stored."""
    s = unicodedata.normalize("NFKC", name or "")
    s = _PAREN_RE.sub("", s)
    s = _SUFFIX_RE.sub("", s)
    return _PUNCT_RE.sub("", s).lower().strip()


def _build():
    by_key, by_code = {}, {}
    for slug, c in COMPANIES.items():
        entry = dict(c, slug=slug)
        for nm in [c.get("name_en"), c.get("name_ja")] + list(c.get("aliases") or []):
            k = match_key(nm)
            if k:
                by_key.setdefault(k, entry)
        if c.get("sec_code"):
            by_code[c["sec_code"]] = entry
    return by_key, by_code


BY_KEY, BY_SEC_CODE = _build()


def lookup(name):
    u"""The curated entry for a name as filed, or None."""
    return BY_KEY.get(match_key(name))


def by_tag(tag):
    u"""Every company carrying a theme tag, as (slug, entry) in file order."""
    return [(s, c) for s, c in COMPANIES.items() if tag in (c.get("tags") or [])]


def tags():
    out = set()
    for c in COMPANIES.values():
        out.update(c.get("tags") or [])
    return sorted(out)


def groups():
    u"""{group: [slug, ...]} for every family named in the list."""
    out = {}
    for slug, c in COMPANIES.items():
        if c.get("group"):
            out.setdefault(c["group"], []).append(slug)
    return out


# --- maintenance -------------------------------------------------------------

def check(db_path=None):
    u"""What the list is missing and what it carries that filings no longer say.

    Reads the filed customer names from the equity database when one is
    available, so an alias that has gone stale — a buyer renamed, a filer
    changing its house style — shows up rather than sitting here forever.
    """
    import duckdb
    db_path = db_path or os.environ.get(
        "EQUITY_DB_PATH",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "equity.duckdb"))
    report = {"companies": len(COMPANIES), "aliases": sum(len(c.get("aliases") or []) for c in COMPANIES.values()),
              "tags": tags(), "groups": sorted(groups())}
    if not os.path.exists(db_path):
        report["note"] = "no equity database; alias check skipped"
        return report
    con = duckdb.connect(db_path, read_only=True)
    try:
        names = {r[0]: r[1] for r in con.execute("""
            WITH latest AS (SELECT doc_id FROM (
                SELECT doc_id, row_number() OVER (PARTITION BY coalesce(sec_code, edinet_code)
                       ORDER BY period_end DESC, filed_date DESC, doc_id DESC) rn
                FROM eq_seg_filings WHERE status IN ('clean','partial')) WHERE rn = 1)
            SELECT c.customer_name, count(*) FROM eq_seg_customers c
            JOIN latest USING(doc_id) WHERE c.year_offset = 0 GROUP BY 1""").fetchall()}
    finally:
        con.close()
    filed_keys = {match_key(n) for n in names}
    stale = [a for c in COMPANIES.values() for a in (c.get("aliases") or [])
             if match_key(a) not in filed_keys]
    unmatched = sorted(((n, k) for n, k in names.items() if not lookup(n)),
                       key=lambda x: -x[1])
    report["aliases_no_longer_filed"] = stale
    report["filed_names_not_in_this_list"] = len(unmatched)
    report["most_named_not_in_this_list"] = [
        {"name": n, "suppliers": k} for n, k in unmatched[:25]]
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Curated company list — status")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--db")
    args = ap.parse_args()
    print(json.dumps(check(args.db), ensure_ascii=False, indent=2))
