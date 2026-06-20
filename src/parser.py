import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from .models import Business


def parse_business_page(html: str, biz_url: str, source_url: str) -> Optional[Business]:
    """Parse a Trustpilot business page HTML into a Business object."""
    if not html:
        return None

    # Use html.parser instead of lxml to avoid C extension build issues on some platforms.
    soup = BeautifulSoup(html, "html.parser")

    title = soup.find("title")
    if title and "Verifying your connection" in title.get_text():
        return None

    # Extract NEXT_DATA json which Trustpilot natively embeds for incredibly robust data extraction!
    data = {}
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        try:
            data = json.loads(script.string)
        except Exception:
            pass

    bu = data.get("props", {}).get("pageProps", {}).get("businessUnit", {}) if data else {}

    # Extract robustly from JSON first
    name = bu.get("displayName")
    if not name:
        name_el = soup.find("h1")
        if name_el:
            span = name_el.find("span")
            name = span.get_text(strip=True) if span else name_el.get_text(strip=True)
            name = re.sub(r"Avis.*", "", name).strip()

    if not name:
        return None

    rating = str(bu.get("trustScore", ""))
    if not rating:
        rating_el = soup.find(attrs={"data-rating-typography": True})
        if rating_el:
            rating = rating_el.get_text(strip=True)

    rating = str(rating).replace(".", ",")

    review_count = str(bu.get("numberOfReviews", ""))
    if not review_count:
        count_el = soup.find(attrs={"data-reviews-count-typography": True})
        if count_el:
            m = re.search(r"([\d\s.,\xa0]+)", count_el.get_text())
            if m:
                review_count = re.sub(r"[^\d]", "", m.group(1))

    # Contact Info
    contact = bu.get("contactInfo", {})
    phone = contact.get("phone", "")
    email = contact.get("email", "")
    website = bu.get("websiteUrl", "")

    # Fallback to DOM for contact info if missing from JSON
    if not phone or not email or not website:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("tel:") and not phone:
                phone = href.replace("tel:", "").strip()
            elif href.startswith("mailto:") and not email:
                email = href.replace("mailto:", "").strip()
            elif href.startswith("http") and "trustpilot.com" not in href and not website:
                if not any(x in href for x in ["facebook", "twitter", "linkedin", "google", "instagram"]):
                    m = re.search(r"[?&]url=([^&]+)", href)
                    website = m.group(1) if m else href

    # Socials - Ignore Trustpilot footer links!
    socials = []
    contact_card = soup.select_one("[class*='contactCard'], [class*='contact']")
    if contact_card:
        for a in contact_card.find_all("a", href=True):
            href = a.get("href", "")
            if any(
                x in href.lower()
                for x in ["facebook.com", "instagram.com", "twitter.com", "linkedin.com", "youtube.com"]
            ):
                if "trustpilot" not in href.lower():
                    socials.append(href)
    social_media_links = ", ".join(list(set(socials)))

    # Category
    categories_list = bu.get("categories", [])
    category = ""
    if categories_list:
        primary = [c for c in categories_list if c.get("isPrimary")]
        if primary:
            category = primary[0].get("name", "")
        else:
            category = categories_list[0].get("name", "")
    if not category:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if "/categories/" in href:
                category = a.get_text(strip=True)
                break

    # Address
    address = ""
    city = contact.get("city", "")
    addr = contact.get("address", "")
    zipCode = contact.get("zipCode", "")
    if city or addr or zipCode:
        address = ", ".join(filter(None, [addr, zipCode, city]))
    if not address:
        for sel in ["address", "[class*='contact'] address", "[class*='address']"]:
            addr_el = soup.select_one(sel)
            if addr_el:
                address = ", ".join([t.strip() for t in addr_el.stripped_strings if t.strip()])
                break

    location_country = contact.get("country", "")
    is_claimed = str(bu.get("isClaimed", False))

    # Review Summary
    summary = ""
    summary_el = soup.select_one("[class*='aiSummary'], [class*='summaryText']")
    if summary_el:
        summary = summary_el.get_text(separator=" ", strip=True)

    # Details
    details = bu.get("description", "")
    if not details:
        details_el = soup.select_one("[class*='aboutCompany'], [class*='companyDescription']")
        if details_el:
            details = details_el.get_text(separator=" ", strip=True)

    # Clean up summary and details from boilerplate
    if summary and ("Pour protéger" in summary or "Pour prot" in summary):
        summary = ""
    if details and ("Pour protéger" in details or "Pour prot" in details or "Trustpilot" in details):
        details = ""

    # Ratings Distribution
    five_star = ""
    one_star = ""
    full_text = soup.get_text(separator=" ")
    m_5 = re.search(r"5(?:\s*étoiles|\-star)\s*(\d+\s*%)", full_text, re.I)
    if m_5:
        five_star = m_5.group(1).replace(" ", "")
    m_1 = re.search(r"1(?:\s*étoile|\-star)\s*(\d+\s*%)", full_text, re.I)
    if m_1:
        one_star = m_1.group(1).replace(" ", "")

    # Latest review
    latest_review_date = ""
    latest_review_text = ""
    reviews = data.get("props", {}).get("pageProps", {}).get("reviews", [])
    if reviews:
        latest = reviews[0]
        latest_review_text = latest.get("text", "")
        latest_review_date = latest.get("dates", {}).get("publishedDate", "")
    else:
        review_cards = soup.select("article")
        if review_cards:
            first_review = review_cards[0]
            p = first_review.find("p")
            if p:
                latest_review_text = p.get_text(strip=True)
            time_el = first_review.find("time")
            if time_el:
                latest_review_date = time_el.get("datetime", "")

    return Business(
        business_url=biz_url,
        business_name=name,
        category=category,
        address=address,
        phone_number=phone,
        email=email,
        website_url=website,
        rating=rating,
        review_count=review_count,
        review_summary=summary,
        company_details=details,
        source_url=source_url,
        is_claimed=is_claimed,
        social_media_links=social_media_links,
        five_star_percentage=five_star,
        one_star_percentage=one_star,
        location_country=location_country,
        latest_review_date=latest_review_date,
        latest_review_text=latest_review_text,
    )
