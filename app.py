from flask import Flask, render_template, request
from ton_script import find_final_owners

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    results = None
    query = ""
    if request.method == "POST":
        query = request.form["query"]
        results = find_final_owners(query)
    return render_template("index.html", query=query, results=results)

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=10000)
