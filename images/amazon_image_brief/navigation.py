"""A navigation carousel is a group of individually editable frame jobs."""
from copy import deepcopy
from .models import ModuleInstance

NAV_CODE = 'APLUS_NAV_CAROUSEL'


def expand_navigation(instances):
    result = []
    for item in instances:
        if item.module_code != NAV_CODE or item.parent_id:
            result.append(item)
            continue
        # Legacy one-picture nav modules become four frames. First ID is retained
        # so existing approved copy and images remain associated with frame one.
        count = item.frame_count if item.frame_count > 1 else 4
        if not isinstance(count, int) or not 1 <= count <= 20:
            raise ValueError('导航轮播帧数必须是1–20之间的整数。')
        for index in range(1, count+1):
            frame = deepcopy(item)
            frame.instance_id = item.instance_id if index == 1 else f'{item.instance_id}__frame_{index:02d}'
            frame.parent_id, frame.frame_index, frame.frame_count = item.instance_id, index, count
            result.append(frame)
    return result


def resize_navigation(instances, parent_id, count):
    if not 1 <= count <= 20:
        raise ValueError('导航轮播生成数量应为1–20张（生成规划上限，非Amazon上传数量承诺）。')
    members = [i for i in instances if i.parent_id == parent_id]
    if not members:
        raise ValueError('没有找到导航轮播组。')
    selected = deepcopy(members[:count])
    occupied = {i.instance_id for i in instances}
    serial = len(members)+1
    while len(selected) < count:
        iid = f'{parent_id}__frame_{serial:02d}'
        serial += 1
        if iid in occupied:
            continue
        occupied.add(iid)
        selected.append(ModuleInstance(iid, NAV_CODE, members[0].channel, parent_id=parent_id))
    for index, item in enumerate(selected, 1):
        item.frame_index, item.frame_count = index, count
    result, inserted = [], False
    for item in instances:
        if item.parent_id == parent_id:
            if not inserted:
                result.extend(selected)
                inserted = True
        else:
            result.append(item)
    return result


def instance_name(instance, base):
    return f'{base} · 第{instance.frame_index}/{instance.frame_count}帧' if instance.parent_id else base
