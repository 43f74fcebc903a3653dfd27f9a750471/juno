from .automatic import AutomaticRoles
from .booster import BoosterRoles
from .reaction import ReactionRoles
from .vanity import VanityRoles


class Roles(BoosterRoles, AutomaticRoles, ReactionRoles, VanityRoles): ...
