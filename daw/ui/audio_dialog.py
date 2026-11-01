"""Options → Audio properties…: which Windows driver type and output device to play through,
how big the buffers are, and what the open device is doing right now."""
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel

from ..audio import devices
from .preferences import BUFFER_SIZES



class AudioPropertiesDialog(QDialog):
    """After exec(): `driver_type`, `device` (index), `buffer_size` (frames, 0 = driver default)
    and `exclusive` (WASAPI exclusive mode) hold the choice."""

    def __init__(self, parent, driver_type, device, buffer_size, exclusive, engine):
        super().__init__(parent)
        self.setWindowTitle("Audio properties")
        self.driver_type, self.device, self.buffer_size, self.exclusive = driver_type, device, buffer_size, exclusive
        form = QFormLayout(self)

        self.types = QComboBox()
        for t in devices.driver_types():
            self.types.addItem(devices.label(t), t)
        form.addRow("Driver type", self.types)
        self.about = QLabel()
        self.about.setWordWrap(True)
        self.about.setProperty("hint", True)  # the skin's hint colour
        form.addRow(self.about)

        self.devices = QComboBox()
        form.addRow("Output device", self.devices)
        self.exclusive_box = QCheckBox("Exclusive mode (lower delay; other programs go silent)")
        self.exclusive_box.setChecked(exclusive)
        form.addRow(self.exclusive_box)

        self.buffers = QComboBox()
        for frames in BUFFER_SIZES:
            text = "Driver default" if frames == 0 else f"{frames} frames  ({frames / engine.sr * 1000:.1f} ms)"
            self.buffers.addItem(text, frames)
        self.buffers.setCurrentIndex(max(0, self.buffers.findData(buffer_size)))
        self.buffers.setToolTip("Smaller: less delay, but more CPU and a higher risk of clicks.\n"
                                "Bigger: fewer audio callbacks, less CPU, more delay.")
        form.addRow("Buffer size", self.buffers)
        hint = QLabel("Smaller buffers: less delay, more CPU and risk of clicks.\nBigger buffers: less CPU, more "
                      "delay. The sample rate is set in the device's own settings.")
        hint.setProperty("hint", True)
        form.addRow(hint)

        stream = engine.stream
        if stream:
            frames = stream.blocksize or "driver's"
            mode = " exclusive" if engine.exclusive else ""
            form.addRow("Now playing", QLabel(f"{devices.label(engine.driver_type)}{mode}: {engine.sr} Hz, "
                                              f"{engine.channels} outputs"
                                              + (f", {engine.inputs} inputs" if engine.inputs else "")
                                              + f", {frames} frames per buffer, "
                                              f"{engine.output_latency() * 1000:.1f} ms output delay"))
        else:
            form.addRow("Now", QLabel("No audio device open"))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.types.currentIndexChanged.connect(self.show_type)
        self.types.setCurrentIndex(max(0, self.types.findData(driver_type)))
        self.show_type()

    def show_type(self):
        """List the chosen driver type's devices (keeping the current one if it is among them)."""
        driver_type = self.types.currentData()
        self.about.setText(devices.DRIVER_TYPES.get(driver_type, ("", ""))[1])
        self.devices.clear()
        for index, name in devices.outputs(driver_type):
            self.devices.addItem(name, index)
        keep = self.devices.findData(self.device)
        self.devices.setCurrentIndex(keep if keep >= 0 else max(0, self.devices.findData(
            devices.default_output(driver_type))))
        self.exclusive_box.setEnabled(driver_type == "Windows WASAPI")

    def accept(self):
        self.driver_type = self.types.currentData()
        self.device = self.devices.currentData()
        self.buffer_size = self.buffers.currentData()
        self.exclusive = self.exclusive_box.isChecked()
        super().accept()
