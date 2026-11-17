"""The appointment's status table: the only place its legal moves are written.

An outcome can be corrected (completed <-> no_show) and either can be called
off, but a cancelled appointment is final: a new one is booked instead.
"""
from config.lifecycle import Lifecycle
from scheduling.models import Appointment

APPOINTMENT_LIFECYCLE = Lifecycle(Appointment, "status", {
    Appointment.SCHEDULED: {Appointment.COMPLETED, Appointment.NO_SHOW,
                            Appointment.CANCELLED},
    Appointment.COMPLETED: {Appointment.NO_SHOW, Appointment.CANCELLED},
    Appointment.NO_SHOW: {Appointment.COMPLETED, Appointment.CANCELLED},
    Appointment.CANCELLED: set(),
}, name="appointment")
