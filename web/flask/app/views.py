from flask import render_template, redirect, url_for, request, safe_join

from app import app
from models_app import Package_app, Version_app, Location, Directory, \
    SourceFile, PackageFolder, InvalidPackageOrVersionError
from forms import SearchForm

@app.context_processor # variables needed by "base.html" skeleton
def skeleton_variables():
    return dict(packages_prefixes = Package_app.get_packages_prefixes(),
                searchform = SearchForm())

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.route('/doc/')
def doc():
    return render_template('doc.html', url_prefix="http://example.net")

@app.route('/')
@app.route('/nav/') # navigation
def index():
    return render_template('index.html')

@app.route('/nav/search/', methods=['GET', 'POST'])
def receive_search():
    searchform = SearchForm(request.form)
    if searchform.validate_on_submit():
        return redirect(url_for("search",
                                packagename=searchform.packagename.data))
    else:
        # we return the form, to display the errors
        return render_template('index.html', searchform=searchform)

@app.route('/nav/search/<packagename>/')
def search(packagename):
    packagename = packagename.replace('%', '').replace('_', '')
    exact_matching = Package_app.query.filter_by(name=packagename).first()
    other_results = Package_app.query.filter(
        Package_app.name.contains(packagename)).order_by(Package_app.name)
    return render_template('search.html',
                           search=packagename,
                           exact_matching=exact_matching,
                           other_results=other_results)

@app.route('/nav/list/')
@app.route('/nav/list/<int:page>/')
def list(page=1):
    packages = Package_app.query.order_by(
        Package_app.name).paginate(page, 20, False)
    return render_template('list.html',
                           packages=packages,
                           page=page)

@app.route('/nav/letter/')
@app.route('/nav/letter/<letter>')
def letter(letter='a'):
    if letter in Package_app.get_packages_prefixes():
        packages = Package_app.query.filter(
            Package_app.name.startswith(letter)).order_by(Package_app.name)
        return render_template("letter.html",
                               packages=packages,
                               letter=letter)
    else:
        return render_template('404.html'), 404


@app.route('/src/<package>/')
@app.route('/src/<package>/<version>/')
@app.route('/src/<package>/<version>/<path:path_to>/', methods=['POST', 'GET'])
def source(package, version=None, path_to=None):
    try:
        location = Location(package, version, path_to)
    except InvalidPackageOrVersionError: # 404
        return render_template("404.html"), 404
    
    if location.ispackage(): # it's a package, we list its versions
        location = PackageFolder(package)
        
        return render_template("source_package.html",
                               package=location.get_package_name(),
                               versions=location.get_versions(),
                               pathl=location.get_path_links())
    
    if location.isdir(): # it's a folder, we list its content
        location = Directory(package, version, path_to)
        
        return render_template("source_folder.html",
                               files=location.get_subfiles(),
                               dirs=location.get_subdirs(),
                               pathl=location.get_path_links(),
                               parentfolder=not(location.is_top_folder()))
                                 # we want '..', except for a package file
    
    elif location.isfile(): # it's a file, we check if it's a text file
        if not(location.istextfile()): # binary file
            return redirect(location.get_raw_url())
        # else: text file, we display the source code
        try:
            highlight = request.args.get('hl')
        except (KeyError, ValueError, TypeError):
            hl = None
        try:
            msg = request.args.get('msg')
        except (KeyError, ValueError, TypeError):
            msg = None
            
        location = SourceFile(package, version, path_to, highlight, msg)
        
        return render_template("source_file.html",
                               code = location.get_code(),
                               nlines=location.get_number_of_lines(),
                               msg=location.get_msgdict(),
                               pathl=location.get_path_links(),
                               raw_url=location.get_raw_url())
    
    else: # 404
        return render_template('404.html'), 404