if __name__ == "__main__":
    import sys, os
    
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(PROJECT_DIR)
    
    from app import app
    app.run(debug=True)
