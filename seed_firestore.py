import sys
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-01-cbb4224a7e0d"
COLLECTION_NAME = "destinations"

SAMPLE_DESTINATIONS = [
    {
        "id": "dest-tokyo-01",
        "name": "Senso-ji Temple & Asakusa",
        "city": "Tokyo",
        "country": "Japan",
        "category": "Culture & Heritage",
        "rating": 4.8,
        "estimated_cost_usd": 25.0,
        "description": "Historic Buddhist temple in Asakusa surrounded by lively market streets selling traditional snacks.",
        "best_season": "Spring / Autumn",
    },
    {
        "id": "dest-paris-01",
        "name": "Eiffel Tower & Seine River Cruise",
        "city": "Paris",
        "country": "France",
        "category": "Sightseeing & Romance",
        "rating": 4.7,
        "estimated_cost_usd": 45.0,
        "description": "Iconic iron lattice tower with sweeping city panoramas followed by a romantic river cruise.",
        "best_season": "Spring / Summer",
    },
    {
        "id": "dest-kyoto-01",
        "name": "Fushimi Inari Shrine & Bamboo Grove",
        "city": "Kyoto",
        "country": "Japan",
        "category": "Nature & Spirituality",
        "rating": 4.9,
        "estimated_cost_usd": 15.0,
        "description": "Famous Kyoto shrine featuring thousands of vermilion torii gates stretching along mountain trails.",
        "best_season": "Autumn / Spring",
    },
    {
        "id": "dest-nyc-01",
        "name": "Central Park & The Met",
        "city": "New York",
        "country": "USA",
        "category": "Parks & Museums",
        "rating": 4.6,
        "estimated_cost_usd": 30.0,
        "description": "Iconic urban green space paired with access to one of the world's finest art museums.",
        "best_season": "Autumn / Spring",
    },
]

def seed():
    db = firestore.Client(project=PROJECT_ID)
    print(f"Seeding Firestore collection '{COLLECTION_NAME}' in project '{PROJECT_ID}'...")
    collection_ref = db.collection(COLLECTION_NAME)
    for item in SAMPLE_DESTINATIONS:
        doc_ref = collection_ref.document(item["id"])
        doc_ref.set(item)
        print(f"  - Added document {item['id']}: {item['name']}")
    print("Seeding completed successfully!")

if __name__ == "__main__":
    seed()
