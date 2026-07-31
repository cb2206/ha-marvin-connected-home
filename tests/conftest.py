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

import enum
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
_module(
    "homeassistant.core",
    HomeAssistant=_stub("HomeAssistant"),
    callback=lambda func: func,
)
_module("homeassistant.config_entries", ConfigEntry=_stub("ConfigEntry"))
_HomeAssistantError = type("HomeAssistantError", (Exception,), {})
_module(
    "homeassistant.exceptions",
    HomeAssistantError=_HomeAssistantError,
    ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (_HomeAssistantError,), {}),
)


class _CoordinatorEntity:
    """Richer than `_stub`: the cover tests instantiate real entity classes,
    which route their ``__init__`` through here and read ``available`` off it.

    Mirrors the sliver of Home Assistant's CoordinatorEntity/Entity surface the
    component actually touches; anything more belongs in a test against a real
    install.
    """

    name: str | None = None
    entity_id: str | None = None
    hass = None

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    def async_write_ha_state(self) -> None:
        pass


class _CoverEntityFeature(enum.IntFlag):
    """Real Home Assistant values, so feature-mask assertions mean something."""

    OPEN = 1
    CLOSE = 2
    SET_POSITION = 4
    STOP = 8


_module(
    "homeassistant.components.cover",
    ATTR_POSITION="position",
    CoverEntity=_stub("CoverEntity"),
    CoverDeviceClass=_Constants(),
    CoverEntityFeature=_CoverEntityFeature,
)
_module(
    "homeassistant.components.persistent_notification",
    async_create=lambda *args, **kwargs: None,
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
class _DataUpdateCoordinator:
    """Permissive enough for the real MarvinCoordinator to subclass and be
    instantiated with fakes, so its decision logic (`degraded_reason`, the
    auth/connectivity distinction, push merging) is tested for real."""

    def __init__(self, *args, **kwargs) -> None:
        self.last_update_success = True
        self.data = None

    def __class_getitem__(cls, item):
        return cls

    def async_set_updated_data(self, data) -> None:
        self.data = data

    async def async_shutdown(self) -> None:
        pass


_module(
    "homeassistant.helpers.update_coordinator",
    CoordinatorEntity=_CoordinatorEntity,
    DataUpdateCoordinator=_DataUpdateCoordinator,
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
_MarvinError = type("MarvinError", (Exception,), {})

_component_pkg = _module(
    "marvin_connected_home",
    MarvinClient=_stub("MarvinClient"),
    MarvinError=_MarvinError,
    # Connectivity failures are the one class the cover is allowed to fall
    # back on, so the subclass relationship matters to the tests. Auth errors
    # are deliberately NOT connection errors.
    MarvinConnectionError=type("MarvinConnectionError", (_MarvinError,), {}),
    MarvinAuthError=type("MarvinAuthError", (_MarvinError,), {}),
    MarvinRealtime=_stub("MarvinRealtime"),
    # The real merge preservation logic lives in the client library and is
    # tested there; the coordinator tests only care *which* asset it is
    # applied to, so pushed-wins is enough.
    merge_assets=lambda existing, pushed: pushed,
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

# Neither `.entity` nor `.coordinator` is stubbed: the cover and coordinator
# tests instantiate the real classes, and the real modules resolve fine
# against the stubs above (CoordinatorEntity, DataUpdateCoordinator,
# DeviceInfo, the library symbols).
