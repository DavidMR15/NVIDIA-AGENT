from fastapi import FastAPI
from auth.router import router as auth_router

app = FastAPI()
app.include_router(auth_router, prefix="/auth")
# For simplicity, we'll have the endpoints at /register, /login, /me without prefix? The spec says POST /register, etc.
# Let's change the router to not have a prefix, or we can adjust the include_router to have no prefix.
# We'll change the include_router to have no prefix and then the routes will be at /register, etc.
# Actually, the spec doesn't specify a prefix, so we can set the router to have no prefix.
# Let's adjust the include_router to not add a prefix.
app.include_router(auth_router)