"""Apple/UI aliases → Strava types. Keep original Apple sport in ledger identities.
Reference: https://developers.strava.com/docs/reference/#api-models-SportType
"""
import re

# Strava key, Chinese UI label, Apple/export names and common selection aliases.
DEFINITIONS = [
 ('Ride','骑行','Cycling|cycling|户外骑行|室内骑行'),
 ('Run','跑步','Running|running|户外跑步|室内跑步'),
 ('Walk','健走','Walking|walking|步行'), ('Hike','远足','Hiking|hiking|徒步'),
 ('Swim','游泳','Swimming|swimming|泳池游泳|开放水域游泳'),
 ('WeightTraining','举重训练','TraditionalStrengthTraining|FunctionalStrengthTraining|StrengthTraining|strength|力量训练|传统力量训练|功能性力量训练'),
 ('HighIntensityIntervalTraining','高强度间歇训练','HIIT|高强度间歇训练'),
 ('Yoga','瑜珈','瑜伽'), ('Pilates','普拉提',''), ('Elliptical','椭圆机','椭圆机训练'),
 ('Rowing','划船','划船机'), ('StairStepper','跳台阶','StairClimbing|Stairs|爬楼梯|楼梯训练|踏步机'),
 ('Dance','舞蹈','SocialDance|CardioDance|舞蹈健身'), ('Badminton','羽毛球',''),
 ('Basketball','篮球',''), ('Cricket','板球',''), ('Golf','高尔夫',''),
 ('Tennis','网球',''), ('TableTennis','乒乓球',''), ('Soccer','足球',''),
 ('Volleyball','排球',''), ('Squash','壁球',''), ('Racquetball','短柄墙球',''),
 ('Pickleball','匹克球',''), ('Padel','笼式网球','板式网球'),
 ('RockClimbing','攀岩','Climbing'), ('AlpineSki','高山滑雪','DownhillSkiing'),
 ('NordicSki','北欧滑雪','CrossCountrySkiing|越野滑雪'),
 ('Snowboard','滑雪板','Snowboarding|单板滑雪'), ('Snowshoe','雪鞋','SnowSportsSnowshoe'),
 ('Sail','航行','Sailing|帆船'), ('Surfing','冲浪',''),
 ('Wheelchair','轮椅','WheelchairWalkPace|WheelchairRunPace'),
 ('Crossfit','全面健身',''),
 ('Workout','锻炼','Other|MixedCardio|CoreTraining|Flexibility|Cooldown|PreparationAndRecovery|其他|混合有氧|核心训练|柔韧性|整理放松'),
 ('EBikeRide','电动自行车骑行','电助力骑行'), ('EMountainBikeRide','电动山地自行车骑行',''),
 ('MountainBikeRide','山地自行车骑行',''), ('GravelRide','砾石路骑行',''),
 ('VirtualRide','模拟骑行',''), ('VirtualRun','模拟跑步',''), ('VirtualRow','虚拟赛艇',''),
 ('TrailRun','越野跑',''), ('Canoeing','独木舟',''), ('Kayaking','皮划艇',''),
 ('StandUpPaddling','立式单桨冲浪','桨板'), ('Kitesurf','风筝冲浪',''),
 ('Windsurf','风帆冲浪',''), ('IceSkate','滑冰',''), ('InlineSkate','直排轮滑',''),
 ('Skateboard','滑板',''), ('RollerSki','四轮滑雪板',''), ('BackcountrySki','野外滑雪',''),
 ('Handcycle','手动自行车','HandCycling'), ('Velomobile','Velomobile',''),
 ('PhysicalTherapy','理疗',''),
]

def key(value):
    return re.sub(r'[\s_\-]+', '', str(value).removeprefix('HKWorkoutActivityType')).casefold()

ALIASES = {key(alias): target for target, label, names in DEFINITIONS
           for alias in [target, label, *names.split('|')] if alias}
LABELS = {target: label for target, label, _ in DEFINITIONS}
# No one-to-one Strava type: preserve the Apple subtype for selection and identity.
FALLBACKS = {
 'AmericanFootball':'美式橄榄球', 'AustralianFootball':'澳式橄榄球', 'Archery':'射箭',
 'Barre':'芭蕾塑形', 'Baseball':'棒球', 'Bowling':'保龄球', 'Boxing':'拳击',
 'Curling':'冰壶', 'DiscSports':'飞盘', 'EquestrianSports':'马术', 'Fencing':'击剑',
 'Fishing':'钓鱼', 'FitnessGaming':'健身游戏', 'Gymnastics':'体操', 'Handball':'手球',
 'Hockey':'曲棍球', 'Hunting':'狩猎', 'JumpRope':'跳绳', 'Kickboxing':'自由搏击',
 'Lacrosse':'长曲棍球', 'MartialArts':'武术', 'MindAndBody':'身心运动',
 'MixedMetabolicCardioTraining':'混合代谢有氧训练', 'Play':'玩耍', 'Rugby':'英式橄榄球',
 'Softball':'垒球', 'StepTraining':'踏板训练', 'TaiChi':'太极', 'TrackAndField':'田径',
 'UnderwaterDiving':'潜水', 'WaterFitness':'水中健身', 'WaterPolo':'水球',
 'WaterSports':'水上运动', 'Wrestling':'摔跤', 'SwimBikeRun':'铁人三项',
 'Transition':'转换阶段', 'DanceInspired':'舞蹈启发训练',
 'PaddleSports':'桨类运动', 'SkatingSports':'滑行运动', 'SnowSports':'雪上运动',
 'SurfingSports':'冲浪类运动', 'CrossTraining':'交叉训练',
}
ALIASES.update({key(alias): target for target,label in FALLBACKS.items() for alias in (target,label)})
AMBIGUOUS = {
    key('Paddling'): ('Canoeing','Kayaking','StandUpPaddling'),
    key('PaddleSports'): ('Canoeing','Kayaking','StandUpPaddling'),
    key('SkatingSports'): ('IceSkate','InlineSkate','Skateboard'),
    key('SnowSports'): ('AlpineSki','NordicSki','BackcountrySki','Snowboard','Snowshoe'),
    key('SurfingSports'): ('Surfing','Kitesurf','Windsurf'),
    key('CrossTraining'): ('Crossfit','Workout'),
}


def canonical(value):
    return ALIASES.get(key(value), str(value).removeprefix('HKWorkoutActivityType'))


def plan(value):
    target = canonical(value)
    ambiguous = AMBIGUOUS.get(key(canonical(value)))
    exact = target in LABELS and target != 'Workout'
    if target not in LABELS:
        target = 'Workout'
    return {'strava_sport': target, 'strava_label': LABELS[target],
            'type_confirmation_required': not exact,
            'type_note': '' if exact else ('原始类型有多种对应：'+', '.join(ambiguous) if ambiguous else '使用通用锻炼类别，需用户确认'),
            'possible_strava_sports': list(ambiguous or [target])}


def relation(local, remote):
    a, b = canonical(local), canonical(remote)
    if a == b:
        return 'approximate' if a == 'Workout' or a not in LABELS else 'exact'
    if b in AMBIGUOUS.get(key(a), ()):
        return 'approximate'
    # Generic classifications can hide a specific workout; retain as a review candidate.
    if a == 'Workout' or b == 'Workout':
        return 'approximate'
    return None


def selected(actual, requested):
    if str(requested).lower() == 'all':
        return True
    # Explicit Apple subtype keeps its specificity; family aliases can select both strength types.
    raw = str(requested).removeprefix('HKWorkoutActivityType')
    raw = {'传统力量训练':'TraditionalStrengthTraining', '功能性力量训练':'FunctionalStrengthTraining',
           '核心训练':'CoreTraining', '混合有氧':'MixedCardio', '其他':'Other'}.get(raw,raw)
    if raw in ('TraditionalStrengthTraining','FunctionalStrengthTraining','CoreTraining','MixedCardio','Other'):
        return actual == raw
    return key(actual) == key(requested) or canonical(actual) == canonical(requested)
