import logging
from .instructions import Instruction, HaltInstruction, Bubble, HaltSignal, RawDependencySignal, JumpSignal
from .memories import Memory, RegisterSet


logger = logging.getLogger(__name__)

_statistics = {
    'cycles': 0,
    'instructions': 0,
}

class Cpu:

    class CpuStatus:
        RUNNING = 0
        STOPPING = 1
        HALTED = 2

    class Pipeline:

        class PipelineStage:
            IF = 1
            ID = 2
            EX = 3
            MEM = 4
            WB = 5

        def __init__(self):
            self._pipeline = {
                self.PipelineStage.IF: Bubble(),
                self.PipelineStage.ID: Bubble(),
                self.PipelineStage.EX: Bubble(),
                self.PipelineStage.MEM: Bubble(),
                self.PipelineStage.WB: Bubble(),
            }

        def fetch(self, next_instruction: Instruction):
            self.__move(self.PipelineStage.IF, self.PipelineStage.ID)
            logger.info("Loading into IF stage instruction '%s'." % next_instruction)
            self.__set(self.PipelineStage.IF, next_instruction)

        def decode(self):
            """
            It is possible that decode() throws a HaltSignal, the instruction will be moved from ID to EX anyway
            """
            instruction = self.__get(self.PipelineStage.ID)

            try:
                signal = None
                instruction.decode()

            except HaltSignal as s:
                signal = s

            except JumpSignal as s:
                signal = s

            self.__move(self.PipelineStage.ID, self.PipelineStage.EX)

            if signal:
                raise signal

        def execute(self):
            instruction = self.__get(self.PipelineStage.EX)
            instruction.execute()
            self.__move(self.PipelineStage.EX, self.PipelineStage.MEM)

        def memory(self):
            instruction = self.__get(self.PipelineStage.MEM)
            instruction.memory()
            self.__move(self.PipelineStage.MEM, self.PipelineStage.WB)

        def writeback(self):
            instruction = self.__get(self.PipelineStage.WB)
            instruction.writeback()

            if not isinstance(instruction, Bubble):
                _statistics['instructions'] += 1

        def is_empty(self):
            """ A pipe is empty if after the HALT instruction there's only BUBBLEs """
            halt_instruction_found = False
            no_more_instructions = True

            for phase, instruction in self._pipeline.items():
                if halt_instruction_found:
                    if not isinstance(instruction, Bubble):  # Normal instruction detected after HALT
                        no_more_instructions = False

                if isinstance(instruction, HaltInstruction):
                    halt_instruction_found = True

            return halt_instruction_found and no_more_instructions

        def flush(self):
            """
            Replaces the instruction in the IF phase with a Bubble
            """
            self.__set(self.PipelineStage.IF, Bubble())

        def stall(self, phase):
            """
            Instead of moving the instruction of the current phase to the next one,
            a bubble is inserted in the next phase and the instructions
            of the previous phases are neither moved nor executed.
            """
            if phase == self.PipelineStage.WB:
                """ Programming error """
                raise RuntimeError

            self.__set(phase+1, Bubble())

        def __move(self, stage_src, stage_dst):
            logger.info("Moving from phase %s to phase %s instruction '%s' ."
                        % (stage_src, stage_dst, self._pipeline[stage_src]))

            self._pipeline[stage_dst] = self._pipeline[stage_src]

        def __get(self, stage):
            return self._pipeline[stage]

        def __set(self, stage, instruction: Instruction):
            self._pipeline[stage] = instruction

        def __repr__(self):
            return ("\n1. %s\n2. %s\n3. %s\n4. %s\n5. %s" %
                    (self.__get(self.PipelineStage.IF),
                     self.__get(self.PipelineStage.ID),
                     self.__get(self.PipelineStage.EX),
                     self.__get(self.PipelineStage.MEM),
                     self.__get(self.PipelineStage.WB)))

    def __init__(self, registers: RegisterSet, memory: Memory):
        self._registers = registers
        self._memory = memory
        self._pc = 0
        self._pipeline = self.Pipeline()
        self._status = self.CpuStatus.HALTED

    def start(self):
        self.set_running()

    def step(self):
        if self.is_halted():
            raise HaltedCpuError()

        logger.info("Processing step %d." % _statistics['cycles'])
        current_phase = None

        try:
            current_phase = self.Pipeline.PipelineStage.WB
            self._pipeline.writeback()

            current_phase = self.Pipeline.PipelineStage.MEM
            self._pipeline.memory()

            current_phase = self.Pipeline.PipelineStage.EX
            self._pipeline.execute()

            current_phase = self.Pipeline.PipelineStage.ID
            self._pipeline.decode()

            if self.is_running():
                " If RUNNING, the next instruction is got from the memory "
                next_instruction = self._memory.get_data(self._pc)
                self._pc += 1
            elif self.is_stopping():
                " If STOPPING, the next instruction is a Bubble "
                next_instruction = Bubble()
            else:
                " Programming error "
                raise RuntimeError()

            current_phase = self.Pipeline.PipelineStage.IF
            self._pipeline.fetch(next_instruction)

        except HaltSignal:
            logger.info("Halt signal received.")
            if self.is_running():
                self.set_stopping()
                self._pipeline.flush()  # Last fetched instruction is wrong, it must be a BUBBLE

            self._pipeline.fetch(Bubble())

        except RawDependencySignal:
            logger.info("RAW dependency signal received.")
            self._pipeline.stall(current_phase)

        except JumpSignal as s:
            logger.info("Jump signal received.")
            self._pipeline.flush()
            self._pc = s.addr

            if self.is_running():
                " If RUNNING, the next instruction is got from the memory "
                next_instruction = self._memory.get_data(self._pc)
                self._pc += 1
            elif self.is_stopping():
                " If STOPPING, the next instruction is a Bubble "
                next_instruction = Bubble()
            else:
                " Programming error "
                raise RuntimeError()

            self._pipeline.fetch(next_instruction)

        finally:
            logger.info(self._pipeline)
            logger.info("Step done.\n\n")

            if self.is_stopping() and self._pipeline.is_empty():
                self.set_halted()

            _statistics['cycles'] += 1

    def is_halted(self):
        return self._status == self.CpuStatus.HALTED

    def is_running(self):
        return self._status == self.CpuStatus.RUNNING

    def is_stopping(self):
        return self._status == self.CpuStatus.STOPPING

    def set_halted(self):
        logger.info("CPU status is now HALTED.")
        self._status = self.CpuStatus.HALTED

    def set_stopping(self):
        logger.info("CPU status is now STOPPING.")
        self._status = self.CpuStatus.STOPPING

    def set_running(self):
        logger.info("CPU status is now RUNNING.")
        self._status = self.CpuStatus.RUNNING


class HaltedCpuError(Exception):
    pass