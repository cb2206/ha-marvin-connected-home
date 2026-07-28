"""Home Assistant stubs, shared by every test module.

The integration cannot be imported without Home Assistant installed, and
installing it to test a lambda table is disproportionate. So the handful of
symbols the component modules import are stubbed here instead.

This lives in `conftest.py` rather than in each test file for a specific
reason: `sys.modules` is process-global, so two test files each installing
their own `homeassistant.const` means whichever imports first wins and the
other's symbols silently vanish. Collecting them once, before any test module
is imported, removes that ordering dependency — add new symbols here as more
platforms get tests.

The stubs are deliberately shallow. Anything that needs real Home Assistant
behaviour belongs in a test against a real install, not here.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path

COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "marvin_connected_home"


def _stub(name: str) -> type:
    """A distinct stand-in class per symbol.

    Distinct matters: entity classes inherit from both a Home Assistant base
    and a coordinator base, and a single shared stub makes that a duplicate
    base class at class-creation time.

    Subscriptable, because the component subclasses generics such as
    `CoordinatorEntity[MarvinCoordinator]`; the parameter is discarded since
    nothing here type-checks at runtime.
    """
    return type(name, (), {"__class_getitem__": classmethod(lambda cls, item: cls)})


class _Constants:
    """Stands in for Home Assistant's device-class / state-class enums.

    Vends any attribute as its own lowercased name, so adding a device class to
    a platform never requires editing this file. Real values are only needed
    where a test asserts on them -- units, for instance -- and those are given
    explicitly rather than derived here.
    """

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return name.lower()


def _module(name: str, **attrs: object) -> types.ModuleType:
    mod = sys.modules.get(name) or types.ModuleType(name)
    for attr, value in attrs.items():
        setattr(mod, attr, value)
    sys.modules[name] = mod
    return mod


@dataclass(frozen=True, kw_only=True)
class EntityDescription:
    """Mirrors the fields the component's descriptions actually set."""

    key: str
    translation_key: str | None = None
    device_class: str | None = None
    entity_category: str | None = None
    entity_registry_enabled_default: bool = True
    native_unit_of_measurement: str | None = None
    state_class: str | None = None
    native_min_value: float | None = None
    native_max_value: float | None = None
    native_step: float | None = None


# --- homeassistant --------------------------------------------------------

for _package in ("homeassistant", "homeassistant.components", "homeassistant.helpers"):
    _pkg_mod = _module(_package)
    _pkg_mod.__path__ = []  # let submodules resolve

_module(
    "homeassistant.const",
    ATTR_ENTITY_ID="entity_id",
    STATE_ON="on",
    STATE_UNAVAILABLE="unavailable",
    STATE_UNKNOWN="unknown",
    EntityCategory=_Constants(),
    PERCENTAGE="%",
    CONCENTRATION_PARTS_PER_MILLION="ppm",
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER="µg/m³",
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT="dBm",
    # Real Home Assistant values, so a test can assert the declared unit is
    # actually Fahrenheit rather than just "not the other stub".
    UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C", FAHRENHEIT="°F"),
)
_module("homeassistant.core", HomeAssistant=_stub("HomeAssistant"))
_module("homeassistant.config_entries", ConfigEntry=_stub("ConfigEntry"))
_module(
    "homeassistant.exceptions",
    HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
)
_module(
    "homeassistant.components.button",
    ButtonEntity=_stub("ButtonEntity"),
    ButtonEntityDescription=EntityDescription,
    ButtonDeviceClass=_Constants(),
)
_module(
    "homeassistant.components.binary_sensor",
    BinarySensorEntity=_stub("BinarySensorEntity"),
    BinarySensorEntityDescription=EntityDescription,
    BinarySensorDeviceClass=_Constants(),
)
_module(
    "homeassistant.components.sensor",
    SensorEntity=_stub("SensorEntity"),
    SensorEntityDescription=EntityDescription,
    SensorDeviceClass=_Constants(),
    SensorStateClass=_Constants(),
)
_module(
    "homeassistant.components.number",
    NumberEntity=_stub("NumberEntity"),
    NumberEntityDescription=EntityDescription,
    NumberMode=_Constants(),
)
_module(
    "homeassistant.components.select",
    SelectEntity=_stub("SelectEntity"),
    SelectEntityDescription=EntityDescription,
)
_module(
    "homeassistant.components.switch",
    SwitchEntity=_stub("SwitchEntity"),
    SwitchEntityDescription=EntityDescription,
)
_module(
    "homeassistant.helpers.entity_platform",
    AddEntitiesCallback=_stub("AddEntitiesCallback"),
)
_module(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=_stub("CoordinatorEntity"),
    DataUpdateCoordinator=_stub("DataUpdateCoordinator"),
    UpdateFailed=type("UpdateFailed", (Exception,), {}),
)
_module(
    "homeassistant.helpers.device_registry",
    DeviceInfo=dict,
    CONNECTION_NETWORK_MAC="mac",
)

# --- marvin_connected_home ------------------------------------------------

# The component package and the client library share a name. Point the name at
# the component directory so `.const`/`.fallback` resolve as submodules without
# executing the component's __init__.py (which would pull in all of Home
# Assistant), and hang the library's symbols off the same module so
# `from marvin_connected_home import MarvinClient` resolves too.
_component_pkg = _module(
    "marvin_connected_home",
    MarvinClient=_stub("MarvinClient"),
    MarvinError=type("MarvinError", (Exception,), {}),
    # Used only as type annotations by the platform modules; tests pass simple
    # namespaces in their place, which is enough to exercise the `value_fn`
    # lambdas that hold the actual logic.
    Asset=_stub("Asset"),
    Device=_stub("Device"),
    Environment=_stub("Environment"),
    Capabilities=_stub("Capabilities"),
    House=_stub("House"),
)
_component_pkg.__path__ = [str(COMPONENT)]

# Pulled in by the entity modules, and heavier than any test here needs.
_module("marvin_connected_home.coordinator", MarvinCoordinator=_stub("MarvinCoordinator"))
_module(
    "marvin_connected_home.entity",
    MarvinAssetEntity=_stub("MarvinAssetEntity"),
    MarvinHouseEntity=_stub("MarvinHouseEntity"),
)
