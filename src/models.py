from dataclasses import dataclass


@dataclass
class Business:
    """Represents a scraped business from Trustpilot."""

    business_url: str = ""
    business_name: str = ""
    category: str = ""
    address: str = ""
    phone_number: str = ""
    email: str = ""
    website_url: str = ""
    rating: str = ""
    review_count: str = ""
    review_summary: str = ""
    company_details: str = ""
    source_url: str = ""
    is_claimed: str = "False"
    social_media_links: str = ""
    five_star_percentage: str = ""
    one_star_percentage: str = ""
    location_country: str = ""
    latest_review_date: str = ""
    latest_review_text: str = ""
