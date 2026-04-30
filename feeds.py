"""
feeds.py — Wasatch Intelligence RSS Feed Registry
--------------------------------------------------
Add, remove, or adjust feeds here. Run aggregator.py to pull them.

Category taxonomy:
  national-brokerage  | News and strategy from national brands operating in the Wasatch Back
                        (Compass, Sotheby's, BHHS, Coldwell Banker, RE/MAX, eXp, etc.)
  local-market        | Wasatch Back listing activity, price trends, development, resort RE news
  agent-intel         | Agent movements, production rankings, team formations, notable closings
  industry-practice   | Best practices for buyer/seller rep, marketing luxury listings, tech tools,
                        CRM/lead-gen, open house, photography/staging
  market-data         | Median prices, days on market, inventory, YoY comparisons, ski resort RE reports
  comp-market         | Mountain resort benchmark markets: Aspen, Jackson Hole, Sun Valley, Steamboat

RSS Research Status:
  ✅ = confirmed live feed
  ❌ = no RSS — see NO_RSS_SOURCES below for workarounds
  ⚠️  = paywalled or requires subscription
  🚫 = hard-blocked (Cloudflare/IP block) — moved to NO_RSS_SOURCES
  🔍 = unverified — confirm before running
"""

# Each entry: (label, rss_url, category, notes)
FEEDS = [

    # ── Real Estate Trade & Agent-Facing Media ────────────────────────────────
    ("Inman News",              "http://feeds.feedburner.com/inmannews",              "industry-practice", "✅ FeedBurner — primary RE trade publication (direct URL blocks scrapers)"),
    ("HousingWire",             "https://www.housingwire.com/feed/",                  "industry-practice", "✅ WordPress RSS — mortgage, market, industry news"),
    ("Keeping Current Matters", "https://www.keepingcurrentmatters.com/feed/",        "industry-practice", "✅ WordPress RSS — agent-facing market education content"),
    ("The Close",               "https://www.theclose.com/feed/",                     "industry-practice", "✅ Confirmed live 2026-04-21 — agent how-to, scripts, tools, team building"),
    ("BiggerPockets Blog",      "https://www.biggerpockets.com/blog/feed/",           "industry-practice", "✅ Confirmed live 2026-04-21 — investor/agent strategy, market analysis"),
    ("WAV Group",               "https://www.wavgroup.com/feed/",                     "industry-practice", "✅ Confirmed live 2026-04-21 — brokerage strategy, MLS tech, industry consulting"),
    ("Luxury Presence Blog",    "https://www.luxurypresence.com/blog/feed/",          "industry-practice", "✅ Confirmed live — luxury agent marketing, websites, lead gen"),
    ("RealTrends",              "https://www.realtrends.com/feed/",                   "industry-practice", "✅ Confirmed live — brokerage strategy, rankings, M&A"),
    ("Tom Ferry Blog",          "https://www.tomferry.com/blog/feed/",                "industry-practice", "✅ Confirmed live — agent coaching, mindset, scripts, production"),

    # ── Luxury & High-End Market Intelligence ────────────────────────────────
    ("Robb Report — Real Estate", "https://robbreport.com/category/real-estate/feed/", "national-brokerage", "✅ Confirmed live 2026-04-21 — ultra-luxury RE, design, architecture, lifestyle"),
    ("Sotheby's Int'l Realty — Extraordinary Living", "https://www.sothebysrealty.com/extraordinary-living-blog/feed/", "national-brokerage", "✅ Confirmed live 2026-04-16 — luxury RE lifestyle, market insights, global SIR brand voice"),
    ("BHHS Utah Properties",       "https://rss.app/feeds/LE60RYOSiR99y9yn.xml",         "national-brokerage", "✅ rss.app feed — BHHS Utah blog: market news, agent content, Wasatch Back coverage, bhhsutah.com"),
    ("Coldwell Banker Blue Matter", "https://blog.coldwellbanker.com/feed/",               "national-brokerage", "✅ Confirmed live — Coldwell Banker blog, market pulse updates"),
    ("RE/MAX Blog",                 "https://blog.remax.com/feed/",                        "national-brokerage", "✅ Confirmed live — RE/MAX newsroom and property content"),

    # ── Local / Wasatch Back Sources ─────────────────────────────────────────
    ("MTN Utah — Park City Real Estate",  "https://www.mtnutah.com/feed/",                    "local-market", "✅ Confirmed live 2026-04-21 — Park City market updates, local RE commentary"),
    ("TownLift — Park City News",         "https://townlift.com/feed/",                       "local-market", "✅ Confirmed live 2026-04-21 — Park City/Summit County local news, RE coverage, community intel"),
    ("TownLift — Real Estate",            "https://townlift.com/category/real-estate/feed/",  "local-market", "✅ Confirmed live 2026-04-21 — RE-only feed: listings, agent spotlights, brokerage news, market reports. Sponsored by Summit SIR."),
    ("Park City Investor",                 "https://rss.app/feeds/7ezhoqqWezqIlpoz.xml",     "local-market", "✅ rss.app feed — Park City investment RE: ski-in/ski-out, resort properties, Canyons/Deer Valley intel"),
    ("Christie's Real Estate — Park City", "https://rss.app/feeds/20iRBHTpmgVzCmoW.xml",     "local-market", "✅ rss.app feed — Park City luxury listings and property intel from christiesrealestatepc.com"),
    ("Heber Valley Life",                  "https://rss.app/feeds/1JfTOHsmKYuK9Z9B.xml",     "local-market", "✅ rss.app feed — Heber Valley community and lifestyle coverage, hebervalleylife.com"),

    # ── Market Data ───────────────────────────────────────────────────────────
    ("Park City Board of Realtors",        "https://rss.app/feeds/txsKHXrxWP90RIxt.xml",     "market-data",  "✅ rss.app feed — quarterly market statistics for Summit & Wasatch Counties, parkcityrealtors.com"),
    ("Utah Association of Realtors",       "https://rss.app/feeds/BS8nfsqcAkLOZ0XD.xml",     "market-data",  "✅ rss.app feed — UAR newsroom: statewide market data, press releases, Realtor community news"),

    # ── Newsletters (via Kill the Newsletter) ────────────────────────────────
    ("The Real Deal",          "https://kill-the-newsletter.com/feeds/7hvjs04p33bdflxovxir.xml", "agent-intel",        "✅ Kill the Newsletter — therealdeal.com daily. Awaiting first delivery."),
    ("BAM / Broke Agent Media", "https://kill-the-newsletter.com/feeds/1ekhn8xifqgy4jxwxiwt.xml", "industry-practice", "✅ Kill the Newsletter — nowbam.com newsletter. Awaiting first delivery."),
    ("Inman Morning Briefing",  "https://kill-the-newsletter.com/feeds/swurxc4o5aploojm40yr.xml", "industry-practice", "✅ Kill the Newsletter — inman.com daily briefing. Awaiting first delivery."),
    ("RealTrends The Broker",   "https://kill-the-newsletter.com/feeds/iqzqswofjf0372ou20gs.xml", "industry-practice", "✅ Kill the Newsletter — realtrends.com newsletter. Awaiting first delivery."),
    ("Tom Ferry Newsletter",    "https://kill-the-newsletter.com/feeds/e167ssaqu2qdiojyo6dd.xml", "industry-practice", "✅ Kill the Newsletter — tomferry.com weekly. Awaiting first delivery."),

    # ── Comp Markets ─────────────────────────────────────────────────────────
    ("Steamboat Pilot & Today",    "https://steamboatpilot.com/feed/",                          "comp-market", "✅ Confirmed live — Steamboat Springs RE and community news"),
    ("Aspen Daily News",           "https://www.aspendailynews.com/feed/",                      "comp-market", "🔍 Aspen comp market — check feed"),
    ("Jackson Hole News & Guide",  "https://www.jhnewsandguide.com/feed/",                      "comp-market", "🔍 Jackson Hole comp market — check feed"),

    # ── Add more feeds below ──────────────────────────────────────────────────
    # ("Publication Name", "https://example.com/feed/", "category", "notes"),
]


# ─────────────────────────────────────────────────────────────────────────────
# Sources WITHOUT standard RSS feeds — workaround required
# ─────────────────────────────────────────────────────────────────────────────
#
# Workaround options:
#   Kill the Newsletter (https://kill-the-newsletter.com)
#     1. Go to kill-the-newsletter.com, create a feed with the source name
#     2. You get a unique @kill-the-newsletter.com email address
#     3. Subscribe the newsletter using that email
#     4. Paste the generated Atom URL into "kill_the_newsletter_feed" below
#     5. Move that entry to FEEDS once it's confirmed active
#
#   rss.app (https://rss.app)
#     1. Go to rss.app, enter the source URL
#     2. Generate a feed and paste the rss.app URL into "generated_feed" below
#     3. Move to FEEDS once confirmed

NO_RSS_SOURCES = [

    # ── No RSS — newsletter/email only ───────────────────────────────────────
    {
        "name": "NAR Realtor Magazine",
        "website": "https://www.nar.realtor/magazine",
        "platform": "Custom CMS",
        "workaround": "Kill the Newsletter — subscribe to their email newsletter",
        "kill_the_newsletter_feed": "",
        "notes": "❌ NAR discontinued RSS feeds. Email newsletter only. Use Kill the Newsletter.",
    },
    {
        "name": "Jackson Hole News & Guide",
        "website": "https://www.jhnewsandguide.com",
        "platform": "Unknown",
        "workaround": "Kill the Newsletter or rss.app",
        "generated_feed": "",
        "notes": "❌ No RSS feed found. Key comp market (Teton/Jackson Hole). Use newsletter or rss.app on their site.",
    },
    {
        "name": "Deseret News — Real Estate",
        "website": "https://www.deseret.com/real-estate/",
        "platform": "Custom CMS",
        "workaround": "rss.app on /real-estate/ section",
        "generated_feed": "",
        "notes": "❌ No RSS feed. Utah statewide RE coverage. Generate via rss.app.",
    },

    # ── National Brokerages — no confirmed RSS ────────────────────────────────
    {
        "name": "Compass Blog",
        "website": "https://www.compass.com/blog/",
        "platform": "Custom CMS / React",
        "workaround": "rss.app — generate from https://www.compass.com/blog/",
        "generated_feed": "",  # ← paste rss.app URL here after setup
        "notes": "❌ No /feed/ path. Compass is a custom React app. Use rss.app.",
    },
    # Sotheby's Int'l Realty Blog — MOVED TO FEEDS (✅ confirmed live 2026-04-16)
    {
        "name": "eXp Realty Blog",
        "website": "https://exprealty.com/blog/",
        "platform": "WordPress (check for Cloudflare block)",
        "workaround": "Try https://exprealty.com/blog/feed/ — if 403, use rss.app",
        "generated_feed": "",
        "notes": "🔍 WordPress likely — test /feed/ path before setting up rss.app.",
    },
    {
        "name": "Engel & Volkers — US Press",
        "website": "https://www.engelvoelkers.com/en-us/press/",
        "platform": "Custom CMS",
        "workaround": "rss.app on their US press/news page",
        "generated_feed": "",
        "notes": "❌ No standard RSS. Local E&V Park City office is a key Wasatch Back presence.",
    },

    # Heber Valley Life — MOVED TO FEEDS (✅ rss.app feed live 2026-04-21)
    # ── Hyperlocal — Wasatch Back ─────────────────────────────────────────────
    # Park City Investor — MOVED TO FEEDS (✅ rss.app feed live 2026-04-21)
    {
        "name": "Summit County Sentinel",
        "website": "https://summitcountysentinel.com/",
        "platform": "Unknown",
        "workaround": "rss.app",
        "generated_feed": "",
        "notes": "❌ Hyperlocal Summit County outlet. Generate via rss.app.",
    },
    # Utah Association of Realtors — MOVED TO FEEDS (✅ rss.app feed live 2026-04-21)
    # Park City Board of Realtors — MOVED TO FEEDS (✅ rss.app feed live 2026-04-21)

    # ── Comp Markets — no confirmed RSS ──────────────────────────────────────
    {
        "name": "Aspen Times — Real Estate",
        "website": "https://www.aspentimes.com/real-estate/",
        "platform": "WordPress (try /real-estate/feed/)",
        "workaround": "Try feed path; fall back to rss.app",
        "generated_feed": "",
        "notes": "🔍 Aspen is the primary comp market benchmark for Park City pricing narratives.",
    },
    {
        "name": "Sun Valley / Idaho Mountain Express",
        "website": "https://www.mtexpress.com/",
        "platform": "Unknown",
        "workaround": "Try /feed/ or rss.app",
        "generated_feed": "",
        "notes": "🔍 Sun Valley / Ketchum — benchmark ski resort market.",
    },

    # ── Email newsletters — Kill the Newsletter setup required ─────────────────
    # Inman Morning Briefing — MOVED TO FEEDS (✅ Kill the Newsletter live 2026-04-21)
    # RealTrends The Broker — MOVED TO FEEDS (✅ Kill the Newsletter live 2026-04-21)
    # Tom Ferry Newsletter — MOVED TO FEEDS (✅ Kill the Newsletter live 2026-04-21)
    # WAV Group — MOVED TO FEEDS (✅ confirmed live 2026-04-21)
    {
        "name": "MoxiWorks Blog",
        "website": "https://moxiworks.com/blog/",
        "platform": "WordPress",
        "workaround": "Try https://moxiworks.com/blog/feed/ — if blocked, use rss.app",
        "generated_feed": "",
        "notes": "Agent productivity tech platform. Good for industry-practice category.",
    },
    # The Real Deal — MOVED TO FEEDS (✅ Kill the Newsletter feed live 2026-04-21, awaiting first delivery)
    # BAM / Broke Agent Media — MOVED TO FEEDS (✅ Kill the Newsletter live 2026-04-21)
    {
        "name": "The Close — Newsletter",
        "website": "https://www.theclose.com/newsletter/",
        "platform": "Email newsletter",
        "workaround": "Kill the Newsletter (blog feed already in FEEDS; this captures their newsletter-exclusive content)",
        "kill_the_newsletter_feed": "",
        "notes": "Optional — blog RSS already covers most content. Newsletter has some exclusives.",
    },
]
