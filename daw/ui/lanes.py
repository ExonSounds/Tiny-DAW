"""The editor's lanes from top to bottom, shared by the timeline, the lane panel and the overview
so they always line up:

    a header lane for each group, right above its tracks (GROUP_H tall)
    a lane for each track, as tall as the track; none for the tracks of a folded group
    under a track showing its automation, a lane for each of its automation lanes
    the master lane at the bottom (MASTER_H)
"""
from dataclasses import dataclass

from .. import config


@dataclass(frozen=True)
class Lane:
    top: int
    height: int
    track: object = None  # a track's lane
    group: object = None  # a group's header lane
    automation: object = None  # an automation lane (model.Automation) of the track `owner`
    owner: object = None
    # none of these: the master lane

    @property
    def bottom(self):
        return self.top + self.height

    @property
    def is_master(self):
        return self.track is None and self.group is None and self.automation is None


def layout(project, top=0):
    """[Lane], starting at y = top; the last one is the master lane."""
    lanes, y, current = [], top, None
    for track in project.tracks:
        if track.group is not None and track.group is not current:
            lanes.append(Lane(y, config.GROUP_H, group=track.group))
            y += config.GROUP_H
        current = track.group
        if current is not None and current.folded:
            continue
        lanes.append(Lane(y, track.height, track=track))
        y += track.height
        if track.show_automation:
            for automation in track.automation:
                lanes.append(Lane(y, automation.height, automation=automation, owner=track))
                y += automation.height
    lanes.append(Lane(y, config.MASTER_H))
    return lanes
