ENTRY(Reset_Handler)

/*
 * CM4 ve los 288 KiB de SRAM D2 mediante el alias 0x10000000. Se reservan los
 * primeros 256 bytes físicos (0x30000000) para un búfer visible por DMA1.
 */
_estack = 0x10048000;
_Min_Heap_Size = 0x0;
_Min_Stack_Size = 0x800;

MEMORY
{
  FLASH       (rx)  : ORIGIN = 0x08100000, LENGTH = 1024K
  RAM_D2_ALIAS(rw)  : ORIGIN = 0x10000100, LENGTH = 0x47F00
  SRAM1_DMA   (rw)  : ORIGIN = 0x30000000, LENGTH = 0x100
  SRAM4       (rw)  : ORIGIN = 0x38000000, LENGTH = 64K
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
  } > RAM_D2_ALIAS AT > FLASH

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
  } > RAM_D2_ALIAS

  .dma_buffer (NOLOAD) :
  {
    . = ALIGN(32);
    __dma_buffer_start__ = .;
    KEEP(*(.dma_buffer))
    KEEP(*(.dma_buffer.*))
    . = ALIGN(32);
    __dma_buffer_end__ = .;
  } > SRAM1_DMA

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
  } > RAM_D2_ALIAS

  /DISCARD/ :
  {
    *(.note.GNU-stack)
  }

  .ARM.attributes 0 : { *(.ARM.attributes) }
}

ASSERT(ADDR(.isr_vector) == 0x08100000,
       "El vector CM4 debe comenzar en Flash bank 2")
ASSERT(ADDR(.dma_buffer) == 0x30000000,
       "El búfer TX debe usar la dirección física visible por DMA1")
ASSERT(SIZEOF(.dma_buffer) <= LENGTH(SRAM1_DMA),
       "El búfer DMA excede su reserva")
ASSERT(ADDR(.shared) == 0x38000000,
       "La cola compartida debe comenzar en SRAM4")
ASSERT(SIZEOF(.shared) <= LENGTH(SRAM4),
       "La región compartida excede SRAM4")
