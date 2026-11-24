"""Starter watchlist: companies whose job boards were confirmed to exist (and were open to India or
remote candidates) when this list was made. `kind` is a rough size label, not a fact: edit it on the
Company list tab. (name, board, slug, kind)
"""
SEED = [
    # India-based startups and scale-ups
    ("Groww", "greenhouse", "groww", "Mid-size"),
    ("Slice", "greenhouse", "slice", "Startup"),
    ("CRED", "lever", "cred", "Mid-size"),
    ("Meesho", "lever", "meesho", "Mid-size"),
    ("Mindtickle", "lever", "mindtickle", "Mid-size"),
    ("Pocket FM", "lever", "pocketfm", "Startup"),
    ("Zeta", "lever", "zeta", "Mid-size"),
    ("Hevo Data", "lever", "hevo", "Startup"),
    ("Atlan", "ashby", "atlan", "Startup"),
    # remote-friendly product companies
    ("PostHog", "ashby", "posthog", "Startup"),
    ("Supabase", "ashby", "supabase", "Startup"),
    ("Linear", "ashby", "linear", "Startup"),
    ("Railway", "ashby", "railway", "Startup"),
    ("Render", "ashby", "render", "Startup"),
    ("Airbyte", "ashby", "airbyte", "Startup"),
    ("Temporal", "ashby", "temporal", "Startup"),
    ("Buffer", "ashby", "buffer", "Startup"),
    ("Netlify", "greenhouse", "netlify", "Startup"),
    ("PlanetScale", "greenhouse", "planetscale", "Startup"),
    ("Neon", "lever", "neon", "Startup"),
    # mid-size companies, several with India offices
    ("Fivetran", "greenhouse", "fivetran", "Mid-size"),
    ("Mixpanel", "greenhouse", "mixpanel", "Mid-size"),
    ("Coursera", "greenhouse", "coursera", "Mid-size"),
    ("GitLab", "greenhouse", "gitlab", "Mid-size"),
    ("Vercel", "greenhouse", "vercel", "Mid-size"),
    ("Airtable", "greenhouse", "airtable", "Mid-size"),
    ("ClickHouse", "ashby", "clickhouse", "Mid-size"),
    ("Sentry", "ashby", "sentry", "Mid-size"),
    ("Webflow", "ashby", "webflow", "Mid-size"),
    ("Amplitude", "ashby", "amplitude", "Mid-size"),
    ("Zapier", "ashby", "zapier", "Mid-size"),
    ("Notion", "ashby", "notion", "Mid-size"),
]
