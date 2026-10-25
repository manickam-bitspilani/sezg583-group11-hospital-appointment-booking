cd patient-service

# create and activate a virtual environment
py -m venv venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1

# install dependencies
pip install -r requirements.txt

# create your local config (only the first time)
Copy-Item .env.example .env

# start the service
python main.py

localhost:8002/api/v1/patients

req body 

{
    "full_name":"test patient",
    "age":30,
    "phone":"9876543210"
}