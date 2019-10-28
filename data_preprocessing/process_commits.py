from git import Repo
import os, sys
from jira import JIRA
import re
import csv
from jira_cache import CachedIssues
from tqdm import tqdm
sys.path.append(os.path.dirname(__file__))
import commit_features as cf
import time

output_dir_eszz = "../Enhanced_SZZ/outputs/hive/"
output_dir_szz = "../SZZ/outputs/hive/"
repo_dir = "../Enhanced_SZZ/repos/hive/"
repo_name = "hive"
print("Initializing repo...")
repo = Repo.init(repo_dir)
print("Initializing JIRA...")
options = {'server': 'https://issues.apache.org/jira'}
jira = JIRA(options=options)
print("Load cached jira issues...")
saved_jira_path = "../Enhanced_SZZ/jira/hive/issue_cache.json"
ISSUES = CachedIssues.load(open(saved_jira_path))

def get_jira_id(commit):
    result = re.search('('+repo_name.upper()+'[-,_]{1}[0-9]+|HADOOP[-,_]{1}[0-9]+)', commit.message, re.IGNORECASE)
    if result is not None:
        return result.group(0).replace("_", "-", 1)
    else:
        return None

def get_jira_issue(commit):
    jira_id = get_jira_id(commit=commit)
    if jira_id is not None:
        jira_issues = [issue for issue in ISSUES if issue.key == jira_id]
        # if the resulting list isn't empty
        if jira_issues:
            return jira_issues[0]
        else:
            return None
    else:
        return None

def get_dictionary(commit, jira_issue):
    d = {}
    d['sha'] = commit.hexsha
    d["num_of_insertions"] = cf.num_of_insertions(commit)
    d["num_of_deletions"] = cf.num_of_deletions(commit)
    d["num_of_changed_files"] = cf.num_of_changed_files(commit)
    d["day_of_week"] = cf.day_of_week(commit)
    d["hour_of_commit"] = cf.hour_of_commit(commit)
    d["solve_time"] = cf.solve_time(commit, jira_issue)
    d["resolution_time"] = cf.resolution_time(jira_issue)
    if d["resolution_time"] is not None:
        d["solve_res_diff"] = abs(d["solve_time"] - d["resolution_time"])
    else:
        d["solve_res_diff"] = None
    d["number_of_comments"] = cf.number_of_comments(jira_issue)
    d["summary"] = cf.summary(jira_issue)
    d["description"] = cf.description(jira_issue)
    d["components"] = cf.components(jira_issue)
    d["affects_versions"] = cf.affects_versions(jira_issue)
    d["comments"] = cf.comments(jira_issue)
    d["number_of_patches"] = cf.number_of_patches(jira_issue)
    d["patch_size_mean"] = cf.patch_size_mean(jira_issue)
    d["patch_size_variance"] = cf.patch_size_variance(jira_issue)
    d["patch_size_rel_variance"] = cf.patch_size_rel_variance(jira_issue)
    d["filepath_contains_test"] = cf.filepath_contains_test(commit)
    return d


def process_commits(commits, path, labels):
    start = time.time()
    jira_issues = map(get_jira_issue, commits)
    columns = ["sha", "num_of_insertions", "num_of_deletions", "num_of_changed_files", "day_of_week", "hour_of_commit",
               "solve_time", "resolution_time", "solve_res_diff", "number_of_comments", "summary", "description",
               "components", "affects_versions", "comments", "number_of_patches", "patch_size_mean",
               "patch_size_variance", "patch_size_rel_variance", "filepath_contains_test"]
    if labels is not None:
        columns.append("label")
    with open(path, "w") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=columns)
        writer.writeheader()
        if labels is not None:
            for row, label in tqdm(zip(map(get_dictionary, commits, jira_issues), labels)):
                row["label"] = label
                writer.writerow(row)
        else:
            for row in tqdm(map(get_dictionary, commits, jira_issues)):
                writer.writerow(row)
    end = time.time()
    print("Time elapsed: ", (end - start))


def get_commits_from_shas(shas):
    return [repo.commit(sha) for sha in shas]

# Processing E-SZZ generated commits
with open(output_dir_eszz + "sha_label_eszz.csv", "r") as csv_file:
    reader = csv.DictReader(csv_file)
    commits = []
    labels = []
    for row in reader:
        commit = repo.commit(row["sha"])
        if get_jira_issue(commit) is not None:
            commits.append(commit)
            labels.append(row["label"])


process_commits(commits, "../commit_classifiers/eszz_data.csv", labels)

# Processing SZZ generated commits
with open(output_dir_szz + "sha_label_szz.csv", "r") as csv_file:
    reader = csv.DictReader(csv_file)
    commits = []
    labels = []
    for row in reader:
        commit = repo.commit(row["sha"])
        if get_jira_issue(commit) is not None:
            commits.append(commit)
            labels.append(row["label"])


process_commits(commits, "../commit_classifiers/szz_data.csv", labels)
