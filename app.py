import os
from flask import Flask, render_template, request
from ton_script import find_final_owners

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    results = None
    query = ""
    error_message = None

    if request.method == "POST":
        query = request.form["query"]
        try:
            results = find_final_owners(query)
        except Exception as e:
            print("Erreur dans find_final_owners:", e)
            results = [{"chemin": [query], "type": "erreur"}]
            error_message = "Impossible de récupérer les données pour cette recherche."

    return render_template("index.html", query=query, results=results, error_message=error_message)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
