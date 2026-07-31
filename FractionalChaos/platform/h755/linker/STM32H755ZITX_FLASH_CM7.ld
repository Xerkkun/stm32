ENTRY(Reset_Handler)

_estack = ORIGIN(RAM_D1) + LENGTH(RAM_D1);
__stack_probe_size__ =
    DEFINED(FC_RUNTIME_PROBE_ENABLED) ? 0x8000 : 0;
__stack_probe_start__ = _estack - __stack_probe_size__;
__stack_probe_end__ = _estack;
_Min_Heap_Size = 0x0;
_Min_Stack_Size = 0x1000;

MEMORY
{
  FLASH   (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
  DTCMRAM (rw)  : ORIGIN = 0x20000000, LENGTH = 128K
  RAM_D1  (rw)  : ORIGIN = 0x24000000, LENGTH = 512K
  SRAM4   (rw)  : ORIGIN = 0x38000000, LENGTH = 64K
}

SECTIONS
{
  .isr_vector :
  {
    . = ALIGN(4);
    KEEP(*(.isr_vector))
    . = ALIGN(4);
  } > FLASH

  .text :
  {
    . = ALIGN(4);
    *(.text)
    *(.text*)
    *(.glue_7)
    *(.glue_7t)
    *(.eh_frame)
    KEEP(*(.init))
    KEEP(*(.fini))
    . = ALIGN(4);
    _etext = .;
  } > FLASH

  .rodata :
  {
    . = ALIGN(4);
    *(.rodata)
    *(.rodata*)
    KEEP(*(.fc_manifest_hash))
    . = ALIGN(4);
  } > FLASH

  .ARM.extab (READONLY) :
  {
    . = ALIGN(4);
    *(.ARM.extab* .gnu.linkonce.armextab.*)
    . = ALIGN(4);
  } > FLASH

  .ARM (READONLY) :
  {
    . = ALIGN(4);
    __exidx_start = .;
    *(.ARM.exidx*)
    __exidx_end = .;
    . = ALIGN(4);
  } > FLASH

  .preinit_array (READONLY) :
  {
    PROVIDE_HIDDEN(__preinit_array_start = .);
    KEEP(*(.preinit_array*))
    PROVIDE_HIDDEN(__preinit_array_end = .);
  } > FLASH

  .init_array (READONLY) :
  {
    PROVIDE_HIDDEN(__init_array_start = .);
    KEEP(*(SORT(.init_array.*)))
    KEEP(*(.init_array*))
    PROVIDE_HIDDEN(__init_array_end = .);
  } > FLASH

  .fini_array (READONLY) :
  {
    PROVIDE_HIDDEN(__fini_array_start = .);
    KEEP(*(SORT(.fini_array.*)))
    KEEP(*(.fini_array*))
    PROVIDE_HIDDEN(__fini_array_end = .);
  } > FLASH

  _sidata = LOADADDR(.data);
  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data)
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } > RAM_D1 AT > FLASH

  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    __bss_start__ = _sbss;
    *(.bss)
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
    __bss_end__ = _ebss;
  } > RAM_D1

  /*
   * Buffer opcional de adquisicion one-shot. CM7 lo llena antes de publicar
   * estados hacia SRAM4, de modo que no comparte trafico con el integrador.
   */
  .capture ALIGN(32) (NOLOAD) :
  {
    . = ALIGN(32);
    __capture_start__ = .;
    KEEP(*(.capture))
    KEEP(*(.capture.*))
    . = ALIGN(32);
    __capture_end__ = .;
  } > RAM_D1

  /*
   * El historial del integrador se mantiene en DTCM: acceso determinista del
   * CM7 y sin tráfico en la matriz AXI durante la suma fraccionaria.
   */
  .solver (NOLOAD) :
  {
    . = ALIGN(32);
    __solver_start__ = .;
    KEEP(*(.solver))
    KEEP(*(.solver.*))
    . = ALIGN(32);
    __solver_end__ = .;
  } > DTCMRAM

  /*
   * NOLOAD impide que el Reset_Handler de cualquiera de los núcleos copie o
   * borre SRAM4. Sólo CM7 inicializa la cola antes de liberar HSEM0.
   */
  .shared ORIGIN(SRAM4) (NOLOAD) :
  {
    . = ALIGN(32);
    __shared_start__ = .;
    KEEP(*(.shared.queue))
    KEEP(*(.shared.flags))
    KEEP(*(.shared))
    KEEP(*(.shared.*))
    . = ALIGN(32);
    __shared_end__ = .;
  } > SRAM4

  ._user_heap_stack (NOLOAD) :
  {
    . = ALIGN(8);
    PROVIDE(end = .);
    PROVIDE(_end = .);
    . = . + _Min_Heap_Size;
    . = . + _Min_Stack_Size;
    . = ALIGN(8);
    __heap_stack_reservation_end__ = .;
  } > RAM_D1

  /DISCARD/ :
  {
    *(.note.GNU-stack)
  }

  .ARM.attributes 0 : { *(.ARM.attributes) }
}

ASSERT(ADDR(.isr_vector) == 0x08000000,
       "El vector CM7 debe comenzar en Flash bank 1")
ASSERT(ADDR(.shared) == 0x38000000,
       "La cola compartida debe comenzar en SRAM4")
ASSERT(SIZEOF(.shared) <= LENGTH(SRAM4),
       "La región compartida excede SRAM4")
ASSERT(SIZEOF(.solver) <= LENGTH(DTCMRAM),
       "El workspace fraccionario excede DTCM")
ASSERT((ADDR(.capture) & 31) == 0,
       "La seccion de captura no esta alineada a cache")
ASSERT(__heap_stack_reservation_end__ <= __stack_probe_start__,
       "RAM_D1 static allocations overlap the 32 KiB CM7 stack probe")
ASSERT(__stack_probe_end__ == _estack,
       "The CM7 stack probe must end at the initial MSP")
