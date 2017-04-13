#!/usr/bin/env python3
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime as dt
from subprocess import run
from traceback import format_exc

import attr
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.keys import Keys

from conf import *


@attr.s
class Activity:
    start_time = attr.ib()
    project = attr.ib()
    issue = attr.ib()
    line = attr.ib()
    end_time = attr.ib(default=None)

    @property
    def duration(self):
        if not self.end_time:
            return 0
        else:
            seconds = (self.end_time - self.start_time).seconds
            minutes = round(seconds / 60.0)
            return minutes


def printerr(*args):
    return print(*args, file=sys.stderr)


def parse_log(lines):
    for line in lines:
        if re.match(r'^\n$', line):
            continue
        try:
            # TODO: more robust parsing - this depends on date format
            date, dow, time, *rest = line.split(' ')
        except ValueError:
            printerr('Error: Could not parse line: {}'.format(line))
            continue
        stime = dt.strptime(' '.join([date, dow, time]), DATETIMEFMT)
        if len(rest) > 1 and re_issue.match(rest[1]):
            project = rest[0].strip()
            issue = re_issue.match(rest[1]).group()
        elif len(rest) == 1:
            project = rest[0].strip()
            if project == NO_BILL:
                issue = NO_BILL
            elif project in BREAKS:
                issue = project
                project = 'breaks'
            else:
                issue = NO_ISSUE
        else:
            project = issue = NO_ISSUE
        yield Activity(stime, project, issue, line)
    # So that end time of last tracked activity is now
    yield Activity(dt.now(), 'breaks', 'done', '')


def check_unbillable(acts):
    err = False
    for act in acts:
        if act.project == NO_ISSUE:
            printerr('\nWARNING: no project - %s' % act.line)
            err = True
        elif act.project in ('breaks', NO_BILL):
            pass
        elif act.issue == NO_ISSUE:
            printerr('\nWARNING: no issue in billable project - %s' % act.line)
            err = True
        elif not re_issue.match(act.issue):
            printerr('\nWARNING: issue is not a redmine ticket - %s' % act.line)
            err = True
        else:  # valid billable activity
            if not act.end_time:
                printerr('\nWARNING: no end time on a billable line - %s' % act.line)
                err = True
    return err


def set_end_times(acts):
    last = next(acts)
    for act in acts:
        if last.start_time.day == act.start_time.day:
            last.end_time = act.start_time
        yield last
        last = act


def sum_activities(acts):
    # Summary for printing (ordered by day first)
    summary = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: 0)))
    # Summary for redmine (ordered by ticket first)
    rmsummary = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: 0)))
    for act in acts:
        if act.duration:
            day = act.start_time.date()
            summary[day][act.project][act.issue] += act.duration
            rmsummary[act.project][act.issue][day] += act.duration
    return summary, rmsummary


def treemap(func, tree):
    """
    Apply a function to all leaves of a tree structure (made up of nested
    dicts or lists).
    """
    _type = type(tree)

    def get_keys(struct):
        if isinstance(struct, list):
            return range(len(struct))
        elif isinstance(struct, dict):
            return struct.keys()
        else:
            raise TypeError('get_keys not applicable to type %s' % type)

    def map_recur(node):
        for k in get_keys(node):
            if isinstance(node[k], _type):
                map_recur(node[k])
            else:
                node[k] = func(node[k])

    map_recur(tree)


def round_summary(summary):
    mins = SUMMARY_ROUND
    treemap(lambda x: math.ceil(x / mins) * mins, summary)


def print_summary(summary):
    total_total = 0
    for k in sorted(summary.keys()):
        print('\n', k.strftime(DATEFMT))
        day = summary[k]
        day_total = 0
        for proj in sorted(day.keys()):
            proj_total = 0
            project = day[proj]
            print('   {}'.format(proj.upper()))
            for issue in sorted(project.keys()):
                time = project[issue]
                proj_total += time
                if proj.lower() != 'breaks':
                    day_total += time
                    total_total += time
                print('     {}: {} minutes'.format(issue, time))
            print('     TOTAL: %0.2f hours' % (proj_total / 60.0))
        print('   DAY TOTAL: %0.2f hours' % (day_total / 60.0))
    print('\nTOTAL: %0.2f hours' % (total_total / 60.0))


def print_redmine(summary):
    for proj in summary:
        for issue in summary[proj]:
            if not re_issue.match(issue):
                continue
            print('\n %s (%s)' % (issue, proj))
            for day in summary[proj][issue]:
                hours = summary[proj][issue][day] / 60.0
                day = day.strftime(DATEFMT)
                print('   %s: %0.2f hours' % (day, hours))


def archive_log():
    with open(ARCHIVE, 'a') as arch, open(LOG, 'r') as log:
        arch.write(log.read())
    with open(LOG, 'w') as log:
        log.write('')


def autoredmine(summary):
    def is_logged_in(driver):
        try:
            driver.find_element_by_xpath('//a[@href="/users/{}"]'.format(USERID))
            return True
        except NoSuchElementException:
            return False

    driver = BROWSER()
    driver.get(REDMINE_URL)

    if not is_logged_in(driver):
        driver.get(REDMINE_URL + '/login')
        uname_field = driver.find_element_by_id('username')
        uname_field.clear()
        uname_field.send_keys(USERNAME)
        pwd_field = driver.find_element_by_id('password')
        pwd_field.click()

    while not is_logged_in(driver):
        print('\nPlease login.')
        time.sleep(10)

    for proj in summary:
        for issue in summary[proj]:
            if not re_issue.match(issue):
                continue
            for date in summary[proj][issue]:
                hours = summary[proj][issue][date] / 60.0
                driver.get(REDMINE_URL + '/issues/{}/time_entries/new'.format(issue))
                date_field = driver.find_element_by_id('time_entry_spent_on')
                date_field.clear()
                date_field.send_keys(date.strftime('%Y-%m-%d'))
                hours_field = driver.find_element_by_id('time_entry_hours')
                hours_field.clear()
                hours_field.send_keys('{:0.2f}'.format(hours))
                hours_field.submit()

    driver.close()
    return True


def main(args):
    if not os.path.exists(BASEDIR):
        os.mkdir(BASEDIR)
    if len(args) == 0:
        with open(LOG, 'r') as f:
            print(f.read())
    elif args[0] == '-a':
        archive_log()
    elif args[0] in ('-s', '-r', '-ar'):
        with open(LOG, 'r') as log:
            activities = [a for a in parse_log(log)]
        summary, rmsummary = sum_activities(set_end_times(iter(activities)))
        if args[0] == '-r':
            round_summary(rmsummary)
            print_redmine(rmsummary)
            check_unbillable(activities)
        elif args[0] == '-ar':
            round_summary(rmsummary)
            print_redmine(rmsummary)
            if not check_unbillable(activities):
                if autoredmine(rmsummary):
                    archive_log()
            else:
                printerr('\nERROR: Will not autofill Redmine until all '
                         'warnings are resolved.')
        else:
            round_summary(summary)
            print_summary(summary)
            check_unbillable(activities)
    elif args[0] == '-e':
        run([EDITOR, LOG])
    else:
        parts = [dt.now().strftime(DATETIMEFMT)] + args
        line = ' '.join(parts) + '\n'
        with open(LOG, 'a') as log:
            log.write(line)
        if AWESOME_WIDGET:
            in_ = AWESOME_WIDGET + ':set_text(" -- ' + ' '.join(args) + ' -- ")'
            in_ = bytes(in_, encoding='utf8')
            run(['awesome-client'], input=in_)


if __name__ == '__main__':
    try:
        main(sys.argv[1:])
        sys.exit(0)
    except Exception as e:
        printerr(format_exc(e))
        sys.exit(1)
