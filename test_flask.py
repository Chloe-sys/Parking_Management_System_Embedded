from flask import Flask
from config import FLASK_CONFIG

app = Flask(__name__)

@app.route('/')
def hello():
    return "Flask is working!"

if __name__ == '__main__':
    print(f"Starting test Flask server on {FLASK_CONFIG['host']}:{FLASK_CONFIG['port']}")
    app.run(**FLASK_CONFIG) 