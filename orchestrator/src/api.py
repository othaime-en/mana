from fastapi import FastAPI
import uvicorn
import os


# Initialize FastAPI app
app = FastAPI(
    title="Self-Healing Orchestrator API",
    description="API for managing self-healing CI/CD deployments",
    version="1.0.0"
)

# API Endpoints
@app.get("/")
def root():
    """Root endpoint"""
    return {
        "service": "Self-Healing Orchestrator",
        "version": "1.0.0",
        "status": "healthy"
    }


@app.get("/health")
def health():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )