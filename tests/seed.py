"""Reusable test dataset. Assumes empty tables (the `db` fixture truncates first).

Covers the shapes the app cares about:
  - a top ad (video, evergreen, impression rank 0),
  - a real carousel (two creatives with distinct text),
  - placement variants (same text/link, different images — NOT a carousel),
  - an inactive ad (no appearance -> no impression rank),
  - a landing page with a snapshot and one linked ad,
  - admin data: pages (tracked/paused), a proxy, a finished collection run.
"""

from __future__ import annotations


def seed_basic(conn) -> dict:
    def company(name, domain):
        return conn.execute(
            "INSERT INTO companies (name, domain) VALUES (%s,%s) RETURNING id",
            (name, domain),
        ).fetchone()["id"]

    northwind = company("Northwind Prep", "northwindprep.example")
    contoso = company("Contoso Academy", "contoso-academy.example")

    def ad(archive, company_id, active, dur, platforms):
        return conn.execute(
            """INSERT INTO ads (ad_archive_id, company_id, is_active, duration_days,
                    publisher_platforms, languages, snapshot_url, ad_snapshot_url,
                    first_seen_at, last_seen_at, delivery_start_time)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now() - interval '120 days',
                       now(), now() - interval '120 days')
               RETURNING id""",
            (archive, company_id, active, dur, platforms, ["en_US"],
             "https://www.facebook.com/ads/library/?id=%s" % archive,
             "https://www.facebook.com/ads/library/?id=%s" % archive),
        ).fetchone()["id"]

    def creative(ad_id, idx, **kw):
        conn.execute(
            """INSERT INTO creatives (ad_id, creative_index, body, title, link_url,
                    image_url, video_url, thumbnail_url, cta_text,
                    media_image_key, media_video_key, media_thumb_key)
               VALUES (%(ad_id)s,%(idx)s,%(body)s,%(title)s,%(link)s,
                       %(image_url)s,%(video_url)s,%(thumbnail_url)s,%(cta)s,
                       %(mik)s,%(mvk)s,%(mtk)s)""",
            {"ad_id": ad_id, "idx": idx, "body": kw.get("body"), "title": kw.get("title"),
             "link": kw.get("link"), "image_url": kw.get("image_url"),
             "video_url": kw.get("video_url"), "thumbnail_url": kw.get("thumbnail_url"),
             "cta": kw.get("cta"), "mik": kw.get("mik"), "mvk": kw.get("mvk"),
             "mtk": kw.get("mtk")},
        )

    def appearance(ad_id, active, sort_index):
        conn.execute(
            "INSERT INTO ad_appearances (ad_id, is_active, sort_index) VALUES (%s,%s,%s)",
            (ad_id, active, sort_index),
        )

    a1 = ad("AD_EVERGREEN", northwind, True, 112, ["FACEBOOK", "INSTAGRAM"])
    creative(a1, 0, body="Method that already got 200k students approved.",
             title="Approved without cramming", cta="Learn more",
             video_url="https://cdn.example/v.mp4", mvk="ads/v.mp4",
             thumbnail_url="https://cdn.example/t.jpg", mtk="ads/t.jpg")
    appearance(a1, True, 0)  # top ad (impression rank 0)

    a2 = ad("AD_CAROUSEL", contoso, True, 73, ["INSTAGRAM"])
    creative(a2, 0, body="Did you know 73% of the approved studied with a method?",
             title="Proven method", cta="Start free",
             image_url="https://cdn.example/i0.jpg", mik="ads/i0.jpg")
    creative(a2, 1, body="Second card of the carousel.", title="",
             image_url="https://cdn.example/i1.jpg", mik="ads/i1.jpg")
    appearance(a2, True, 1)

    a3 = ad("AD_CEMETERY", northwind, False, 6, ["FACEBOOK"])
    creative(a3, 0, body="The five mistakes that fail the essay.",
             title="Essay: mistakes that fail you", cta="Read now",
             image_url="https://cdn.example/dead.jpg", mik="ads/dead.jpg")
    # no appearance -> inactive, no sort_index

    # placement variants: SAME text/link/cta, different images -> NOT a carousel
    a4 = ad("AD_VARIANTS", northwind, True, 45, ["FACEBOOK", "INSTAGRAM"])
    for j in range(3):
        creative(a4, j, body="Same ad, different sizes.", title="Intensive",
                 cta="Sign up", link="https://northwindprep.example/promo",
                 image_url="https://cdn.example/var%d.jpg" % j, mik="ads/var%d.jpg" % j)
    appearance(a4, True, 2)

    # admin data
    conn.execute("INSERT INTO pages (company_id, page_id, page_name, is_tracked) VALUES (%s,%s,%s,%s)",
                 (northwind, "111", "Northwind Prep", True))
    conn.execute("INSERT INTO pages (company_id, page_id, page_name, is_tracked) VALUES (%s,%s,%s,%s)",
                 (contoso, "222", "Contoso Academy Official", False))
    conn.execute("INSERT INTO proxies (url, enabled) VALUES (%s,%s)", ("proxy.example:8080", True))
    conn.execute(
        """INSERT INTO collection_runs (started_at, finished_at, total_ads_collected,
                new_ads_discovered, companies_with_results)
           VALUES (now()-interval '1 hour', now()-interval '50 minutes', 42, 5, 2)"""
    )

    # landing page: link the evergreen ad's creative + a snapshot (drives lp_activity)
    lp = conn.execute(
        "INSERT INTO landing_pages (company_id, url_canonical) VALUES (%s,%s) RETURNING id",
        (northwind, "https://northwindprep.example/exam-prep"),
    ).fetchone()["id"]
    conn.execute("UPDATE creatives SET landing_page_id=%s WHERE ad_id=%s", (lp, a1))
    conn.execute(
        "INSERT INTO page_snapshots (landing_page_id, content_hash, headline, description) VALUES (%s,%s,%s,%s)",
        (lp, "hash-1", "Approval guaranteed", "Pass the exam in 90 days."),
    )

    conn.commit()
    return {
        "companies": {"northwind": northwind, "contoso": contoso},
        "evergreen": "AD_EVERGREEN", "carousel": "AD_CAROUSEL",
        "cemetery": "AD_CEMETERY", "variants": "AD_VARIANTS", "lp": lp,
    }
