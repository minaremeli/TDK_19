import datetime
import pytz
import re
import numpy as np

####################################################


def num_of_insertions(commit):
    return commit.stats.total['insertions']


def num_of_deletions(commit):
    return commit.stats.total['deletions']


def num_of_changed_files(commit):
    return commit.stats.total['files']


def day_of_week(commit):
    return commit.committed_datetime.astimezone(pytz.utc).weekday()


def hour_of_commit(commit):
    return commit.committed_datetime.astimezone(pytz.utc).hour


def solve_time(commit, jira_issue):
    committed = commit.committed_datetime.astimezone(pytz.utc)
    created = datetime.datetime.strptime(jira_issue.fields.created, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(pytz.utc)
    delta = committed - created
    return delta.total_seconds()


def resolution_time(jira_issue):
    res = jira_issue.fields.resolutiondate
    cre = jira_issue.fields.created

    if res is not None and cre is not None:
        resolved = datetime.datetime.strptime(res, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(pytz.utc)
        created = datetime.datetime.strptime(cre, '%Y-%m-%dT%H:%M:%S.%f%z').astimezone(pytz.utc)
        delta = created - resolved
        return abs(delta.total_seconds())
    return None


def number_of_comments(jira_issue):
    total = jira_issue.fields.comment.total
    if total is not None:
        return total
    return None


def summary(jira_issue):
    return jira_issue.fields.summary


def description(jira_issue):
    return jira_issue.fields.description


def components(jira_issue):
    components = jira_issue.fields.components
    if len(components) == 0:
        return None
    component_names = [component.name for component in components]
    return ";".join(component_names)


def affects_versions(jira_issue):
    fix_versions = jira_issue.fields.fixVersions
    version_names = [version.name for version in fix_versions]
    if len(version_names) == 0:
        return None
    return ";".join(version_names)


def comments(jira_issue):
    user_comments = [comment for comment in jira_issue.fields.comment.comments if comment.author.name != "hiveqa"]
    if len(user_comments) == 0:
        return None
    return "\n".join([c.body for c in user_comments])


def number_of_patches(jira_issue):
    # include patches that have .patch extension
    pattern = r'.*\.patch$'

    attachments = jira_issue.fields.attachment
    filtered_attachments = []

    for a in attachments:
        matches = re.findall(pattern, a.filename)
        if matches is not None:
            filtered_attachments.append(a)

    return len(filtered_attachments)


def _patch_sizes(jira_issue):
    return np.array([a.size for a in jira_issue.fields.attachment])


def patch_size_mean(jira_issue):
    sizes = _patch_sizes(jira_issue)
    if len(sizes) != 0:
        return np.mean(sizes)
    else:
        return None


def patch_size_variance(jira_issue):
    sizes = _patch_sizes(jira_issue)
    if len(sizes) != 0:
        return np.std(sizes)
    else:
        return None


def patch_size_rel_variance(jira_issue):
    sizes = _patch_sizes(jira_issue)
    if len(sizes) != 0:
        return np.std(sizes) / np.mean(sizes)
    else:
        return None


def filepath_contains_test(commit):
    file_paths = list(commit.stats.files.keys())

    return int(any(map(lambda x: "test" in x.lower(), file_paths)))
