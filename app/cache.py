import json
import redis.asyncio as aioredis

# Connect to the Redis background service running on your machine
redis_client = aioredis.from_url("redis://localhost:6379", decode_responses=True)

async def set_course_cache(class_num: str, data: dict, expire_seconds: int = 1800):
    # Convert your clean python dictionary/object into a text string
    json_data = json.dumps(data)
    # Save it in memory under a unique key, and tell it to self-destruct in 30 mins (1800s)
    await redis_client.set(f"course:{class_num}", json_data, ex=expire_seconds)

async def get_course_cache(class_num: str) -> dict | None:
    # Look in the RAM notepad for this course key
    cached_data = await redis_client.get(f"course:{class_num}")
    if cached_data:
        return json.loads(cached_data) # Convert string back to a Python dictionary
    return None # Cache miss! Not found.