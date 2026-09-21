import uvicorn
from mock_ehr.config import config

def main():
    print(f"Starting {config.SERVICE_NAME} on http://{config.HOST}:{config.PORT}")
    uvicorn.run("mock_ehr.main:app", host=config.HOST, port=config.PORT, reload=True)

if __name__ == "__main__":
    main()
