from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dsxlineage.api import endpoints
from dsxlineage.db.database import engine, Base

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="DataWise API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(endpoints.router, prefix="/api")

@app.get("/")
def read_root():
    return {"message": "Welcome to DataWise API"}
