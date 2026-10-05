# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

MODEL = "gemini-2.5-flash"
FIRESTORE_PROJECT_ID = "qwiklabs-gcp-01-ef7c0599d1e3"
GCS_BUCKET_NAME = "gourmet-guide-media-qwiklabs-gcp-01-ef7c0599d1e3"
MEMORY_BANK_ID = "5163606770710478848"


async def generate_memories_callback(callback_context: CallbackContext):
    """WRITE: After each turn, send the session to Memory Bank for durable memory extraction."""
    try:
        await callback_context.add_session_to_memory()
    except Exception as e:
        print(f"Memory extraction skipped or unavailable: {e}")
    return None


def memory_bank_service_builder():
    """Builds Vertex AI Memory Bank service for deployed containers or local runners."""
    return VertexAiMemoryBankService(
        project=FIRESTORE_PROJECT_ID,
        location="us-east1",
        agent_engine_id=MEMORY_BANK_ID,
    )

# Load Agent Engine resource name from deployment_metadata.json if present
AGENT_ENGINE_RESOURCE_NAME = None
metadata_path = os.path.join(os.path.dirname(__file__), "..", "deployment_metadata.json")
if os.path.exists(metadata_path):
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
            AGENT_ENGINE_RESOURCE_NAME = metadata.get("remote_agent_runtime_id")
    except Exception:
        pass

code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=AGENT_ENGINE_RESOURCE_NAME
)


def get_firestore_client() -> firestore.Client:
    return firestore.Client(project=FIRESTORE_PROJECT_ID)


def search_recipes(cuisine: str = "", dietary_tag: str = "") -> str:
    """Searches for recipes in the Firestore database filtered by cuisine or dietary preferences.

    Args:
        cuisine: Optional cuisine filter (e.g. Italian, Mediterranean, Thai).
        dietary_tag: Optional dietary tag filter (e.g. vegan, gluten-free, keto, vegetarian).

    Returns:
        A formatted list of matching recipes with their IDs, titles, cuisine, prep time, and difficulty.
    """
    db = get_firestore_client()
    docs = db.collection("recipes").stream()
    matches = []

    cuisine_clean = cuisine.lower().strip() if cuisine else ""
    tag_clean = dietary_tag.lower().strip() if dietary_tag else ""

    for doc in docs:
        data = doc.to_dict()
        doc_id = doc.id
        doc_cuisine = str(data.get("cuisine", "")).lower()
        doc_tags = [str(t).lower() for k in ["dietary_tags", "tags"] if k in data for t in data[k]]

        match_cuisine = not cuisine_clean or cuisine_clean in doc_cuisine
        match_tag = not tag_clean or any(tag_clean in t for t in doc_tags)

        if match_cuisine and match_tag:
            matches.append(
                f"- ID: {doc_id} | Title: {data.get('title')} | Cuisine: {data.get('cuisine')} | "
                f"Prep: {data.get('prep_time_minutes')} mins | Difficulty: {data.get('difficulty')} | "
                f"Tags: {', '.join(data.get('dietary_tags', []))}"
            )

    if not matches:
        return f"No recipes found matching criteria (cuisine='{cuisine}', tag='{dietary_tag}')."
    return "Found matching recipes:\n" + "\n".join(matches)


def get_recipe_details(recipe_id: str) -> str:
    """Fetches the full ingredients and cooking instructions for a specific recipe by ID.

    Args:
        recipe_id: The unique recipe identifier (e.g. creamy-tuscan-chicken, thai-green-curry).

    Returns:
        Detailed recipe information including title, ingredients, and preparation steps.
    """
    db = get_firestore_client()
    doc_ref = db.collection("recipes").document(recipe_id.strip())
    doc = doc_ref.get()

    if not doc.exists:
        return f"Recipe with ID '{recipe_id}' was not found in Firestore."

    data = doc.to_dict()
    ingredients_str = "\n".join(f"  • {item}" for item in data.get("ingredients", []))
    tags_str = ", ".join(data.get("dietary_tags", []))

    return (
        f"📖 Recipe Details for: {data.get('title')}\n"
        f"Cuisine: {data.get('cuisine')} | Prep Time: {data.get('prep_time_minutes')} mins | Difficulty: {data.get('difficulty')}\n"
        f"Dietary Tags: {tags_str}\n\n"
        f"Ingredients:\n{ingredients_str}\n\n"
        f"Instructions:\n{data.get('instructions')}"
    )


def add_recipe(
    recipe_id: str,
    title: str,
    cuisine: str,
    prep_time_minutes: int,
    difficulty: str,
    ingredients: str,
    instructions: str,
    dietary_tags: str = "",
) -> str:
    """Adds or updates a recipe in the Firestore database.

    Args:
        recipe_id: Unique slug/ID for the recipe (e.g., garlic-shrimp-pasta).
        title: Display title of the recipe.
        cuisine: Cuisine style (e.g. Italian, Mexican, Asian).
        prep_time_minutes: Preparation time in minutes.
        difficulty: Skill level ('Easy', 'Medium', 'Hard').
        ingredients: Comma-separated list of ingredients.
        instructions: Step-by-step cooking instructions.
        dietary_tags: Comma-separated dietary tags (e.g., gluten-free, keto, vegan).

    Returns:
        Confirmation message upon saving to Firestore.
    """
    db = get_firestore_client()
    ingredients_list = [i.strip() for i in ingredients.split(",") if i.strip()]
    tags_list = [t.strip().lower() for t in dietary_tags.split(",") if t.strip()]

    recipe_data = {
        "recipe_id": recipe_id.strip(),
        "title": title.strip(),
        "cuisine": cuisine.strip(),
        "prep_time_minutes": int(prep_time_minutes),
        "difficulty": difficulty.strip(),
        "ingredients": ingredients_list,
        "instructions": instructions.strip(),
        "dietary_tags": tags_list,
    }

    db.collection("recipes").document(recipe_id.strip()).set(recipe_data)
    return f"Successfully saved recipe '{title}' (ID: {recipe_id}) to Firestore database!"


def get_ingredient_substitutions(ingredient: str, diet_reason: str = "") -> str:
    """Finds culinary ingredient substitutions and conversion ratios for cooking and baking.

    Args:
        ingredient: The original ingredient needing a substitute (e.g., heavy cream, eggs, buttermilk, soy sauce, butter).
        diet_reason: Optional reason for substitution (e.g., vegan, dairy-free, gluten-free, out of stock, nut allergy).

    Returns:
        A list of recommended substitute options with precise measurements and cooking tips.
    """
    subs_db = {
        "heavy cream": [
            "1 cup = 3/4 cup milk + 1/3 cup melted butter (for cooking)",
            "1 cup = 1 cup coconut cream (vegan / dairy-free)",
            "1 cup = 1 cup blended silken tofu (vegan / low-fat)",
        ],
        "egg": [
            "1 egg = 1 tbsp flaxseed meal + 3 tbsp water (flax egg, best for baking)",
            "1 egg = 1/4 cup unsweetened applesauce (for moist baked goods)",
            "1 egg = 1/4 cup mashed banana (adds mild banana flavor)",
            "1 egg = 3 tbsp aquafaba (chickpea liquid, great for meringues)",
        ],
        "eggs": [
            "1 egg = 1 tbsp flaxseed meal + 3 tbsp water (flax egg, best for baking)",
            "1 egg = 1/4 cup unsweetened applesauce (for moist baked goods)",
            "1 egg = 3 tbsp aquafaba (chickpea liquid)",
        ],
        "buttermilk": [
            "1 cup = 1 tbsp lemon juice or white vinegar + milk to equal 1 cup (let sit 5 mins)",
            "1 cup = 1 cup plain yogurt or sour cream thinned with milk",
        ],
        "butter": [
            "1:1 ratio = Coconut oil (melted or solid)",
            "1:1 ratio = Extra virgin olive oil or avocado oil (for savory cooking)",
            "1 cup butter = 3/4 cup vegetable oil (for baking)",
        ],
        "soy sauce": [
            "1:1 ratio = Tamari (gluten-free)",
            "1:1 ratio = Coconut aminos (soy-free & lower sodium)",
            "1:1 ratio = Worcestershire sauce + splash of water",
        ],
        "garlic": [
            "1 clove garlic = 1/8 tsp garlic powder",
            "1 clove garlic = 1/2 tsp garlic paste or minced garlic",
            "1 clove garlic = 1/4 tsp asafoetida / hing powder (for onion/garlic allergy)",
        ],
        "sour cream": [
            "1:1 ratio = Plain Greek yogurt",
            "1:1 ratio = Blended cottage cheese with 1 tsp lemon juice",
            "1:1 ratio = Coconut cream + 1 tsp lemon juice (dairy-free)",
        ],
    }

    ing_clean = ingredient.lower().strip()
    reason_clean = diet_reason.lower().strip()

    matched_key = None
    for k in subs_db:
        if k in ing_clean or ing_clean in k:
            matched_key = k
            break

    if matched_key:
        options = subs_db[matched_key]
        if reason_clean:
            filtered = [opt for opt in options if reason_clean in opt.lower()]
            if filtered:
                options = filtered

        formatted_opts = "\n".join(f"  • {opt}" for opt in options)
        return f"🧂 Recommended Substitutes for '{ingredient}'" + (f" ({diet_reason})" if diet_reason else "") + f":\n{formatted_opts}"

    return (
        f"🧂 General Substitution Guidance for '{ingredient}':\n"
        f"  • Dairy/Cream swap: Try full-fat coconut milk, plain Greek yogurt, or oat milk.\n"
        f"  • Binder/Egg swap: Use 1 tbsp ground flaxseed + 3 tbsp warm water, or mashed banana.\n"
        f"  • Flour swap: 1:1 Gluten-Free baking flour or almond flour.\n"
        f"  • Salt/Umami swap: Tamari, nutritional yeast, or miso paste."
    )


def generate_recipe_image(recipe_title: str, prompt_description: str = "") -> str:
    """Generates an AI image of a dish and uploads it to Cloud Storage for public web rendering.

    Args:
        recipe_title: Display title of the dish (e.g. Creamy Tuscan Garlic Chicken).
        prompt_description: Optional extra details for visual styling (e.g., garnished with fresh basil, overhead studio lighting).

    Returns:
        The public HTTP URL of the generated dish image.
    """
    client = genai.Client()
    full_prompt = f"Professional studio food photography of {recipe_title}. {prompt_description}".strip()

    try:
        result = client.models.generate_images(
            model="imagen-3.0-generate-002",
            prompt=full_prompt,
            config=dict(number_of_images=1, output_mime_type="image/png"),
        )
        image_bytes = result.generated_images[0].image.image_bytes

        clean_slug = "".join(c if c.isalnum() else "_" for c in recipe_title.lower()).strip("_")
        filename = f"recipes/{clean_slug}.png"

        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type="image/png")

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
        return f"🖼️ Generated public dish image for '{recipe_title}': {public_url}"
    except Exception as e:
        return f"Failed to generate image: {e}"


def generate_recipe_video(
    recipe_title: str,
    prompt_description: str = "",
    tool_context: ToolContext = None,
) -> str:
    """Generates a short food preparation/plating video for a dish using Google's Omni model (gemini-omni-flash-preview) in the global region.

    Args:
        recipe_title: Display title of the dish (e.g., Creamy Tuscan Garlic Chicken).
        prompt_description: Optional extra details for visual video styling (e.g., slow-motion pour of garlic sauce over sizzling chicken).
        tool_context: ADK ToolContext used to save the video artifact in the Playground.

    Returns:
        The public HTTPS URL of the generated recipe video in Cloud Storage.
    """
    client = genai.Client(vertexai=True, project=FIRESTORE_PROJECT_ID, location="global")
    full_prompt = f"Cinematic food preparation video of {recipe_title}. {prompt_description}".strip()

    try:
        operation = client.models.generate_videos(
            model="gemini-omni-flash-preview",
            prompt=full_prompt,
        )

        while not operation.done:
            time.sleep(5)
            operation = client.operations.get(operation)

        generated_video = operation.response.generated_videos[0]
        video_bytes = getattr(getattr(generated_video, "video", None), "video_bytes", None)
        if not video_bytes:
            try:
                video_bytes = client.files.download(file=generated_video.video)
            except Exception:
                video_bytes = client.files.download(file=generated_video)

        clean_slug = "".join(c if c.isalnum() else "_" for c in recipe_title.lower()).strip("_")
        if not clean_slug:
            clean_slug = "recipe_video"

        # 1. Save artifact to Playground's Artifacts panel via tool_context
        if tool_context and hasattr(tool_context, "save_artifact"):
            try:
                tool_context.save_artifact(
                    filename=f"{clean_slug}.mp4",
                    artifact=types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"),
                )
            except Exception as e:
                print(f"Failed to save artifact in tool_context: {e}")

        # 2. Upload video bytes to public GCS bucket
        filename = f"recipes/{clean_slug}.mp4"
        storage_client = storage.Client(project=FIRESTORE_PROJECT_ID)
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(video_bytes, content_type="video/mp4")

        public_url = f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{filename}"
        return f"🎥 Generated recipe video for '{recipe_title}': {public_url}"
    except Exception as e:
        return f"Failed to generate video: {e}"


def scale_recipe_servings(recipe_id: str, target_servings: int = 4) -> str:
    """Scales recipe ingredients and portion sizes for a target number of servings.

    Args:
        recipe_id: The unique identifier of the recipe in Firestore (e.g. creamy-tuscan-chicken).
        target_servings: Desired number of servings (e.g. 2, 6, 8).

    Returns:
        Scaled recipe quantities and portion details.
    """
    db = get_firestore_client()
    doc_ref = db.collection("recipes").document(recipe_id.strip())
    doc = doc_ref.get()

    if not doc.exists:
        return f"Recipe '{recipe_id}' not found in Firestore database."

    data = doc.to_dict()
    base_servings = data.get("servings", 4)
    factor = target_servings / base_servings if base_servings > 0 else 1.0

    return (
        f"📏 Portion Scaling for '{data.get('title')}' ({target_servings} servings, scale factor: {factor:.2f}x):\n"
        f"  • Base Servings: {base_servings}\n"
        f"  • Target Servings: {target_servings}\n"
        f"  • Base Ingredients:\n" +
        "\n".join(f"    - {item}" for item in data.get("ingredients", []))
    )


def fetch_external_recipes(query: str = "pasta") -> str:
    """Fetches global recipes and meal ideas from TheMealDB public REST API.

    Args:
        query: Recipe keyword or main ingredient to search for (e.g. pasta, chicken, curry, seafood, tacos).

    Returns:
        A formatted list of global recipes with ingredients, cuisine, photo, and video guide URLs.
    """
    api_key = os.getenv("THEMEALDB_API_KEY", "1")
    encoded_query = urllib.parse.quote(query.strip())
    url = f"https://www.themealdb.com/api/json/v1/{api_key}/search.php?s={encoded_query}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GourmetGuide-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        meals = data.get("meals")
        if not meals:
            return f"No global recipes found on TheMealDB for '{query}'."

        results = []
        for meal in meals[:3]:
            name = meal.get("strMeal")
            category = meal.get("strCategory")
            area = meal.get("strArea")
            thumb = meal.get("strMealThumb")
            yt = meal.get("strYoutube")

            ingredients = []
            for i in range(1, 21):
                ing = meal.get(f"strIngredient{i}")
                meas = meal.get(f"strMeasure{i}")
                if ing and ing.strip():
                    m_str = meas.strip() if meas else ""
                    ingredients.append(f"{m_str} {ing.strip()}".strip())

            ing_str = ", ".join(ingredients[:6]) + ("..." if len(ingredients) > 6 else "")

            item_str = (
                f"🍲 {name} ({area} {category})\n"
                f"   - Ingredients: {ing_str}\n"
                f"   - Photo: {thumb}\n"
            )
            if yt:
                item_str += f"   - Video Guide: {yt}\n"
            results.append(item_str)

        return f"🌍 Global Recipes from TheMealDB for '{query}':\n\n" + "\n".join(results)
    except Exception as e:
        return f"Failed to fetch external recipes: {e}"


schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

a2ui_instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are GourmetGuide, an enthusiastic and expert AI Personal Chef and Recipe Assistant! "
        "Your goal is to help home cooks discover, customize, and plan meals tailored to their dietary needs and preferences."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        "{\"Image\": {\"url\": {\"literalString\": \"https://...\"}}}. Never point an "
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "CRITICAL ALLERGY & DIETARY SAFETY: Always remember and respect all stated user allergies (e.g., peanuts, tree nuts, dairy, gluten, shellfish, eggs, soy, sesame) and dietary restrictions across sessions. "
        "When suggesting recipes or meal plans, always consult preloaded memories and NEVER suggest recipes containing a user's known allergens. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


root_agent = Agent(
    # Keep in sync with agents-cli-manifest.yaml: agents-cli derives this name
    # from the project `name:` recorded there, and telemetry reports it as
    # gen_ai.agent.name.
    name="gourmet_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=a2ui_instruction,
    tools=[
        search_recipes,
        get_recipe_details,
        add_recipe,
        get_ingredient_substitutions,
        generate_recipe_image,
        generate_recipe_video,
        scale_recipe_servings,
        fetch_external_recipes,
        PreloadMemoryTool(),
    ],
    after_agent_callback=generate_memories_callback,
    after_model_callback=a2ui_callback,
    code_executor=code_executor,
)

app = App(
    root_agent=root_agent,
    name="app",
)

