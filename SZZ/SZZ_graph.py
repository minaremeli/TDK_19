import pickle

from git import Repo
import os, sys
from parse import *
from jira import JIRA, JIRAError
import re
import itertools
from jira_cache import CachedIssues
import time
from datetime import datetime
import multiprocessing as mp
import pandas as pd
from graph_tool.all import *

REPO_INFO = {
    "hive": {"url": "https://github.com/apache/hive.git",
             "jira_project_name": "HIVE"}
}

REPO_BASE_DIR = "repos/"

OUTPUTS_DIR = "outputs/"

JIRA_DIR = "jira/"

def get_jira_id(commit):
    result = re.search('('+repo_name.upper()+'[-,_]{1}[0-9]+|HADOOP[-,_]{1}[0-9]+)', commit.message, re.IGNORECASE)
    if result is not None:
        return result.group(0).replace("_", "-", 1)
    else:
        return None

def get_jira_issue(commit, lock=None):
    global ISSUES

    jira_id = get_jira_id(commit=commit)
    if jira_id is not None:
        if lock is not None:
            lock.acquire()
            jira_issues = [issue for issue in ISSUES if issue.key == jira_id]
            lock.release()
        else:
            jira_issues = [issue for issue in ISSUES if issue.key == jira_id]
        # if the resulting list isn't empty
        if jira_issues:
            return jira_issues[0]
        else:
            return None
    else:
        return None

def get_commit_diff_string(commit, repo):
    commit_diff = None
    commit_sha = str(commit.hexsha)
    try:
        # commit has parent
        if len(commit.parents) > 0:
            commit_diff = repo.git.diff('--diff-filter=MRC', commit_sha + '^', commit_sha)
        # commit has no parent
        else:
            commit_diff = repo.git.diff('--diff-filter=MRC', commit_sha)
    except Exception as e:
        print(e)
    return commit_diff


def gen_file_unit(diff):
    pattern = r'(^---[\s\S]*?)(?=^diff)|(^---[\s\S]*)$'
    compiled_pattern = re.compile(pattern=pattern, flags=re.MULTILINE)
    for match in re.finditer(compiled_pattern, diff):
        yield match.group(0) or match.group(1)

def gen_change_unit(file_unit):
    pattern = r'(^@@ [\s\S]*?)(?=^@@ )|(^@@ [\s\S]*)$'
    compiled_pattern = re.compile(pattern=pattern, flags=re.MULTILINE)
    for match in re.finditer(compiled_pattern, file_unit):
        yield match.group(0) or match.group(1)

def gen_line_unit(change_unit):
    pattern = r'^(\d+.*)'
    compiled_pattern = re.compile(pattern=pattern, flags=re.MULTILINE)
    for match in re.finditer(compiled_pattern, change_unit):
        yield match.group(0)


def number_lines(matchobj, bugfix_revision):
    #  number which denotes the starting line
    if bugfix_revision:
        start_line = int(matchobj.group(4))
    else:
        start_line = int(matchobj.group(2))
    # this is the changed code
    code = matchobj.group(6)
    new_code_string = matchobj.group(1)
    for line in code.split('\n'):
        if line != '':
            new_line = str(start_line) + ' ' + line
            new_code_string = new_code_string + '\n' + new_line
            start_line += 1
    return new_code_string

def parse_filename(file_unit, parent):
    if parent:
        pattern = r'\-\-\- a/(.*)'
    else:
        pattern = r'\+\+\+ b/(.*)'
    compiled_pattern = re.compile(pattern=pattern)
    if re.search(compiled_pattern, file_unit):
        return re.search(compiled_pattern, file_unit).group(1)
    else:
        raise Exception("File name not found in this string:\n" + file_unit)

def gen_change_type(change_units):
    for change_unit in change_units:
        added_lines_pattern = r'\n\+{1}.*'
        added_lines_pattern = re.compile(pattern=added_lines_pattern)
        removed_lines_pattern = r'\n-{1}.*'
        removed_lines_pattern = re.compile(pattern=removed_lines_pattern)
        if re.search(added_lines_pattern, change_unit) and re.search(removed_lines_pattern, change_unit):
            yield 'm'
        elif re.search(added_lines_pattern, change_unit):
            yield 'a'
        elif re.search(removed_lines_pattern, change_unit):
            yield 'd'


def gen_numbered_diffs(change_units, bugfix_revision):
    for change_unit in change_units:
        # print(change_unit)
        pattern = r'(@@ -([\d]+),?([\d]*) \+([\d]+),?([\d]*) @@).*\n(([ \-+].*\n?)*)'
        compiled_pattern = re.compile(pattern=pattern, flags=re.MULTILINE)
        result = ''
        for item in re.finditer(compiled_pattern, change_unit):
            new_output_chunk = number_lines(item, bugfix_revision)
            result = '\n' + new_output_chunk
        # print(result)
        yield result


def gen_blamed_commits(lines_to_blame, commit_sha, filename, repo):
    for startLine, offset in lines_to_blame:
        lines = list(range(startLine,startLine+offset))
        for l in lines:
            log_string = repo.git.log('-L ' + str(l) + ',' + str(l) + ':' + filename,
                                      str(commit_sha))
            pattern = r'^commit ([\w\d]{40})'
            compiled_pattern = re.compile(pattern, re.MULTILINE)
            shas = re.findall(compiled_pattern, log_string)

            for blame_commit_sha in shas:
                # don't include first match if that's the bugfix commit itself!
                if blame_commit_sha == commit_sha:
                    continue
                b_commit = repo.commit(rev=blame_commit_sha)
                yield b_commit
                return


def is_refactor(lines, revision, file, refactorings):
    candidates = refactorings[(refactorings.sha == revision) &
                              (refactorings.file == file)]
    l = [list(range(begin, end + 1)) for begin, end in zip(candidates.begin, candidates.end)]
    l_flat = [item for sublist in l for item in sublist]
    l_flat = set(l_flat)
    # print("%s: %str: " % (file, str(l_flat)))

    # because all([]) evaluates to true
    if len(l_flat) == 0:
        return False

    is_refactor = all(line_num in l_flat for line_num in lines)
    return is_refactor


def commit_filter_has_jira(commits):
    for commit in commits:
        if get_jira_issue(commit=commit) is not None:
            yield commit


def commit_filter_jira_type_is_bug(commits):
    for commit in commits:
        issue = get_jira_issue(commit)
        if issue.fields.issuetype.name == "Bug":
            yield commit


def commit_filter_committed_before_jira_creation(commits, creation):
    for commit in commits:
        commit_date_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(commit.committed_date))
        commit_datetime = datetime.strptime(commit_date_str, '%Y-%m-%d %H:%M:%S')
        if commit_datetime < creation:
            yield commit

def fu_filter_file_creation(file_units):
    # filter out file units that contain file creation
    # --- /dev/null
    for file_unit in file_units:
        pattern = r'--- /dev/null'
        compiled_pattern = re.compile(pattern=pattern)
        if not re.search(compiled_pattern, file_unit):
            yield file_unit

def fu_filter_testfiles(file_units):
    # filter out testfiles
    for file_unit in file_units:
        pattern = r'--- .*/test/.*|--- .*/itests/.*|--- .*/testutils/.*'
        compiled_pattern = re.compile(pattern=pattern)
        if not re.search(compiled_pattern, file_unit):
            yield file_unit

def fu_filter_filetypes(file_units):
    # leave files ending in .java, .g, .g4
    for file_unit in file_units:
        pattern = r'--- .*/.*\.java|--- .*/.*\.g|--- .*/.*\.g4'
        compiled_pattern = re.compile(pattern=pattern)
        if re.search(compiled_pattern, file_unit):
            yield file_unit


def cu_filter_lines_with_minus(change_units):
    # take out lines starting with '-' prefix
    for change_unit in change_units:
        pattern = r'\n\-{1}.*'
        compiled_pattern = re.compile(pattern=pattern, flags=re.MULTILINE)
        yield re.sub(compiled_pattern, '', change_unit)


def cu_filter_lines_with_plus(change_units):
    # take out lines starting with '-' prefix
    for change_unit in change_units:
        pattern = r'\n\+{1}.*'
        compiled_pattern = re.compile(pattern=pattern, flags=re.MULTILINE)
        yield re.sub(compiled_pattern, '', change_unit)

def lu_filter_comments(line_units):
    # filter comments
    for line in line_units:
        pattern = r'\d+ [\+\- ] *\/\/.*|\d+ [\+\- ] *\/\*.*|\d+ [\+\- ] *\*.*'
        compiled_pattern = re.compile(pattern=pattern)
        if not re.search(compiled_pattern, line):
            yield line


def lu_filter_blank_lines(line_units):
    # filter blank lines
    for line in line_units:
        pattern = r'\d+ [\+\- ] *$'
        compiled_pattern = re.compile(pattern=pattern)
        if not re.search(compiled_pattern, line):
            yield line

def lu_filter_imports(line_units):
    # filter blank lines
    for line in line_units:
        pattern = r'\d+ [\+\- ]import.*'
        compiled_pattern = re.compile(pattern=pattern)
        if not re.search(compiled_pattern, line):
            yield line


def lu_filter_context(line_units):
    for line in line_units:
        pattern = r'\d+ [\+\-].*'
        compiled_pattern = re.compile(pattern=pattern)
        if re.search(compiled_pattern, line):
            yield line


def get_line_num(line):
    pattern = r'\d+'
    compiled_pattern = re.compile(pattern=pattern)
    if re.search(compiled_pattern, line):
        return int(re.search(compiled_pattern, line).group(0))
    else:
        raise Exception("Number not found in: \n" + line)


def collect_lines_to_blame(line_units):
    lines = []
    previous_num = None
    start_line = None
    plus_N = None
    for line_unit in line_units:
        line_num = get_line_num(line=line_unit)
        if not previous_num:
            previous_num = line_num
            start_line = line_num
            plus_N = 1
        else:
            if line_num == previous_num + 1:
                if not start_line:
                    start_line = previous_num
                else:
                    if not plus_N:
                        plus_N = 1
                    else:
                        plus_N += 1
            else:
                if start_line and plus_N:
                    lines.append((start_line, plus_N))
                    start_line = line_num
                    plus_N = 1
                else:
                    lines.append((line_num, 1))

            previous_num = line_num
    if start_line and plus_N:
        lines.append((start_line, plus_N))

    return lines


def get_jira_creation_datetime(commit, lock):
    jira_issue = get_jira_issue(commit=commit, lock=lock)
    created = jira_issue.fields.created
    return datetime.strptime(created.split(".")[0], '%Y-%m-%dT%H:%M:%S')


def get_blamed_shas(sha, repo, lock):
    print(mp.current_process())
    blamed_commits_all = []

    print("\n--- Processing commit (" + sha + ")")
    commit = repo.commit(sha)

    # time of jira creation
    creation = get_jira_creation_datetime(commit=commit, lock=lock)

    commit_diff = get_commit_diff_string(commit=commit, repo=repo)

    ###########################
    # FILE UNITS
    ###########################
    file_units = gen_file_unit(diff=commit_diff)

    ###########################
    # FILE UNIT FILTERS
    ###########################
    fu_filters = [fu_filter_testfiles,
                  fu_filter_filetypes]
    for f in fu_filters:
        file_units = f(file_units=file_units)

    for file_unit in file_units:
        # parse filename
        bugfix_filename = parse_filename(file_unit, parent=False)
        parent_filename = parse_filename(file_unit, parent=True)

        ###########################
        # CHANGE UNITS
        ###########################
        change_units = gen_change_unit(file_unit=file_unit)

        change_units_copy, change_units = itertools.tee(change_units)
        change_types = gen_change_type(change_units=change_units_copy)

        change_units_bugfix, change_units_parent = itertools.tee(change_units)

        ###########################
        # CHANGE UNIT FILTERS
        ###########################
        cu_filters = [cu_filter_lines_with_minus]
        for f in cu_filters:
            change_units_bugfix = f(change_units=change_units_bugfix)

        # number lines (new revision line numbers)
        change_units_bugfix = gen_numbered_diffs(change_units=change_units_bugfix, bugfix_revision=True)

        cu_filters = [cu_filter_lines_with_plus]
        for f in cu_filters:
            change_units_parent = f(change_units=change_units_parent)

        # number lines (parent revision line numbers)
        change_units_parent = gen_numbered_diffs(change_units=change_units_parent, bugfix_revision=False)

        for change_unit in change_units_parent:
            ###########################
            # LINE UNITS
            ###########################
            line_units = gen_line_unit(change_unit=change_unit)

            ###########################
            # LINE UNIT FILTERS
            ###########################
            lu_filters = [lu_filter_context]

            for f in lu_filters:
                line_units = f(line_units=line_units)

            dummy, line_units = itertools.tee(line_units)
            lines_to_blame = collect_lines_to_blame(line_units=dummy)

            ###########################
            # BLAME COMMITS
            ###########################

            # get blamed commits, and also filter blamed commits that are refactors
            blamed_commits = gen_blamed_commits(lines_to_blame=lines_to_blame, commit_sha=sha + '^',
                                                filename=parent_filename, repo=repo)

            # filter out commits that were created after the creation of the bug report jira ticket
            blamed_commits = commit_filter_committed_before_jira_creation(blamed_commits, creation)

            blamed_commits_all.extend([c.hexsha for c in blamed_commits])

        for change_unit, change_type in zip(change_units_bugfix, change_types):
            # skip additions
            if change_type != "a":
                ###########################
                # LINE UNITS
                ###########################
                line_units = gen_line_unit(change_unit=change_unit)

                ###########################
                # LINE UNIT FILTERS
                ###########################

                lu_filters = [lu_filter_context]

                for f in lu_filters:
                    line_units = f(line_units=line_units)

                dummy, line_units = itertools.tee(line_units)
                lines_to_blame = collect_lines_to_blame(line_units=dummy)

                ###########################
                # BLAME COMMITS
                ###########################

                # get blamed commits
                blamed_commits = gen_blamed_commits(lines_to_blame=lines_to_blame, commit_sha=sha,
                                                    filename=bugfix_filename, repo=repo)

                # filter out commits that were created after the creation of the bug report jira ticket
                blamed_commits = commit_filter_committed_before_jira_creation(blamed_commits, creation)

                ###########################
                # BLAME COMMIT FILTERS
                ###########################
                filters = [commit_filter_has_jira]
                for f in filters:
                    blamed_commits = f(blamed_commits)

                blamed_commits_all.extend([c.hexsha for c in blamed_commits])

    blamed_commits_all = set(blamed_commits_all)
    return tuple([sha, blamed_commits_all])

def add_vertex_properties(v, commit):
    commit_sha = str(commit.hexsha)

    jira_id = get_jira_id(commit=commit)
    jira_issue = get_jira_issue(commit=commit)
    v_properties['jiraid'][v] = jira_id
    v_properties['jira_type'][v] = jira_issue.fields.issuetype
    v_properties['summary'][v] = jira_issue.fields.summary
    versions = jira_issue.fields.versions
    version_names = [version.name for version in versions]
    v_properties['affected_versions'][v] = ", ".join(version_names)
    fix_versions = jira_issue.fields.fixVersions
    version_names = [version.name for version in fix_versions]
    v_properties['fixed_versions'][v] = ", ".join(version_names)
    v_properties['created_date'][v] = jira_issue.fields.created

    v_properties['sha'][v] = commit_sha
    issue_links = jira_issue.fields.issuelinks
    links = [link for link in issue_links if link.type.inward == "is caused by" or
             link.type.inward == "is broken by"]
    causes = [link.inwardIssue.key for link in links if "inwardIssue" in link.raw.keys()]
    v_properties['caused_by'][v] = ", ".join(causes)
    v_properties['commit_date'][v] = commit.committed_datetime


if __name__ == '__main__':

    repo_name = "hive"

    if repo_name not in REPO_INFO.keys():
        raise ValueError("No such repo. Existing repos are: ", REPO_INFO.keys())

    output_dir_path = OUTPUTS_DIR + repo_name + "/"
    if not os.path.exists(output_dir_path):
        print("Creating path for labeled commits... '" + output_dir_path + "'")
        os.makedirs(output_dir_path)

    cloned_repo_base_path = REPO_BASE_DIR + repo_name + "/"
    if not os.path.exists(cloned_repo_base_path):
        print("Creating path for local cloned repository... '" + cloned_repo_base_path + "'")
        os.makedirs(cloned_repo_base_path)

    jira_path = JIRA_DIR + repo_name + "/"
    if not os.path.exists(jira_path):
        print("Creating path for saved jira issues... '" + jira_path + "'")
        os.makedirs(jira_path)

    repo_url = REPO_INFO[repo_name]["url"]
    # if repo hasn't been cloned yet, then clone
    if not os.path.exists(cloned_repo_base_path + ".git"):
        print("Cloning repo...")
        Repo.clone_from(repo_url, cloned_repo_base_path, branch='master')


    # initialize repo
    print("Initializing repo...")
    repo = Repo.init(cloned_repo_base_path)

    # initialize JIRA
    print("Initializing JIRA...")
    options = {'server': 'https://issues.apache.org/jira'}
    jira = JIRA(options=options)

    # load issues
    jira_issue_path = jira_path + 'issue_cache.json'
    if not os.path.isfile(jira_issue_path):
        try:
            ISSUES = jira.search_issues("project=" + repo_name.upper(), maxResults=False, fields="*all")
            print("Issues loaded from server! Caching to file for later use...")
            cached = CachedIssues(ISSUES)
            cached.dump(open(jira_issue_path, 'w'))
        except JIRAError as e:
            if e.status_code == 429:
                print("Got 429 (rate-limited) response from server.")
                print(e.text)
                exit_msg = "Exiting " + time.asctime(time.localtime(time.time()))
                sys.exit(exit_msg)
            else:
                print(e.text)
        except Exception as e:
            print("Some other exception occured.")
            print(e)

    else:
        print("Load issues from cache.")
        ISSUES = CachedIssues.load(open(jira_issue_path))

    # create graph (directed)
    g = Graph(directed=True)

    v_properties = {}
    e_properties = {}

    v_properties['sha'] = g.new_vertex_property("string")
    v_properties['jiraid'] = g.new_vertex_property("string")
    v_properties['jira_type'] = g.new_vertex_property("string")
    v_properties['summary'] = g.new_vertex_property("string")
    v_properties['affected_versions'] = g.new_vertex_property("string")
    v_properties['fixed_versions'] = g.new_vertex_property("string")
    v_properties['created_date'] = g.new_vertex_property("string")
    v_properties['caused_by'] = g.new_vertex_property("string")
    v_properties['commit_date'] = g.new_vertex_property("string")

    ###########################
    # COMMITS
    ###########################
    commits = repo.iter_commits()

    ###########################
    # COMMIT FILTERS
    ###########################
    commit_filters = [commit_filter_has_jira,
                      commit_filter_jira_type_is_bug]
    for f in commit_filters:
        commits = f(commits=commits)

    shas = [commit.hexsha for commit in commits]

    m = mp.Manager()
    lock = m.Lock()

    args = zip(shas, [repo] * len(shas), [lock] * len(shas))
    with mp.Pool(mp.cpu_count()) as p:
        res = p.starmap(get_blamed_shas, args)

    # filter None values
    res = list(filter(None, res))

    with open(output_dir_path + "res.pickle", "wb") as file:
        pickle.dump(res, file)

    stored_commits = list()
    already_blamed_commits = set()
    print("Begin building graph...")
    for item in res:
        print(item)
        commit_sha, blamed_shas = item[0], item[1]
        commit = repo.commit(commit_sha)
        if commit_sha not in stored_commits:
            v = g.add_vertex()
            add_vertex_properties(v=v, commit=commit)
            stored_commits.append(commit_sha)
        else:
            v = g.vertex(stored_commits.index(commit_sha))
        for blamed_commit_sha in blamed_shas:
            b_commit = repo.commit(blamed_commit_sha)
            has_jira = get_jira_issue(b_commit) is not None
            print("\t- Processing bug inducing commit (" + blamed_commit_sha + ")")
            if blamed_commit_sha not in stored_commits:
                bug_source = None
                if has_jira:
                    bug_source = g.add_vertex()
                    add_vertex_properties(v=bug_source, commit=b_commit)
                    stored_commits.append(blamed_commit_sha)
            else:
                bug_source = g.vertex(stored_commits.index(blamed_commit_sha))
            if bug_source is not None:
                edge = g.add_edge(source=bug_source, target=v)

    # add properties
    for prop_name, prop in v_properties.items():
        g.vertex_properties[prop_name] = prop

    for prop_name, prop in e_properties.items():
        g.edge_properties[prop_name] = prop

    # save graph
    g.save(output_dir_path + "my_graph.graphml", fmt="graphml")














