import sys
from google.cloud import firestore

PROJECT_ID = "qwiklabs-gcp-01-ef7c0599d1e3"

def seed_firestore():
    print(f"Seeding Firestore in project: {PROJECT_ID}")
    db = firestore.Client(project=PROJECT_ID)

    recipes = [
        {
            "recipe_id": "creamy-tuscan-chicken",
            "title": "Creamy Tuscan Garlic Chicken",
            "cuisine": "Italian",
            "prep_time_minutes": 25,
            "difficulty": "Easy",
            "ingredients": [
                "1.5 lbs chicken breast",
                "1 cup heavy cream",
                "1/2 cup sun-dried tomatoes",
                "2 cups spinach",
                "3 cloves garlic",
                "1 tbsp olive oil",
            ],
            "instructions": "Sear chicken in olive oil. Sauté garlic and sun-dried tomatoes. Add cream, spinach, and return chicken to sauce.",
            "dietary_tags": ["keto", "gluten-free", "low-carb"],
        },
        {
            "recipe_id": "mediterranean-quinoa-bowl",
            "title": "Mediterranean Quinoa Power Bowl",
            "cuisine": "Mediterranean",
            "prep_time_minutes": 20,
            "difficulty": "Easy",
            "ingredients": [
                "1 cup cooked quinoa",
                "1/2 cup cherry tomatoes",
                "1/2 cup cucumber",
                "1/4 cup kalamata olives",
                "1/4 cup feta cheese",
                "2 tbsp lemon dressing",
            ],
            "instructions": "Layer cooked quinoa in a bowl. Top with fresh vegetables and feta. Drizzle with lemon dressing.",
            "dietary_tags": ["vegetarian", "gluten-free"],
        },
        {
            "recipe_id": "thai-green-curry",
            "title": "Thai Green Vegetable Curry",
            "cuisine": "Thai",
            "prep_time_minutes": 30,
            "difficulty": "Medium",
            "ingredients": [
                "14 oz coconut milk",
                "2 tbsp green curry paste",
                "1 cup tofu or chickpeas",
                "1 cup bell peppers",
                "Thai basil leaves",
            ],
            "instructions": "Sauté curry paste in coconut milk. Add tofu and vegetables, then remaining coconut milk. Simmer 15 mins.",
            "dietary_tags": ["vegan", "gluten-free", "dairy-free"],
        },
    ]

    collection_ref = db.collection("recipes")
    for r in recipes:
        doc_id = r["recipe_id"]
        collection_ref.document(doc_id).set(r)
        print(f"  ✓ Saved recipe: {r['title']} ({doc_id})")

    print("Firestore seeding complete!")

if __name__ == "__main__":
    seed_firestore()
