"""Readable evidence behind a task, with model reasoning separated from stored facts."""
from . import brain, db, loop, overlap, prereq, priority


def detail(con, task_id):
    row=con.execute('SELECT * FROM tasks WHERE id=?',(task_id,)).fetchone()
    if not row:raise ValueError('Task not found.')
    task=dict(row); focus=brain.focus(con)
    recommendation=next((r for r in focus.get('recommended',[])+focus.get('ai_queue',[]) if r['task_id']==task_id),{})
    rationale=recommendation.get('rationale') or {}
    goal=con.execute('SELECT title,why FROM goals WHERE id=?',(task['goal_id'],)).fetchone()
    dependencies=[]
    for value in (task['depends_on'] or '').split(','):
        if value.strip().isdigit():
            dep=con.execute('SELECT id,title,status FROM tasks WHERE id=?',(int(value),)).fetchone()
            if dep:dependencies.append(dict(dep))
    factors=priority.explain(con,row,loop.load_constitution().get('priority')).split(' is on top: ',1)[-1]
    return {'task':task,'goal':dict(goal) if goal else None,'dependencies':dependencies,
            'recommended':bool(recommendation) and task['status'] not in {'done','dropped','blocked'},
            'why':recommendation.get('why','Not in Atlas’s current recommendations.'),
            'rationale':rationale,'evaluated_at':focus.get('at',''),'priority_factors':factors,
            'focus': db.get_state(con,'management_focus',''),
            'overlap': overlap.get(con,'task',task_id),
            'prereq': prereq.for_task(con,task_id)}  # what has to be there first (skill tree, open dependencies, sandbox)
