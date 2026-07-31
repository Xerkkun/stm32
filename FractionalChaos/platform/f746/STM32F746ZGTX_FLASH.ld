ENTRY(Reset_Handler)

_Min_Heap_Size = 0;
_Min_Stack_Size = 0x2000;

MEMORY
{
    FLASH   (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
    DTCMRAM (rw)  : ORIGIN = 0x20000000, LENGTH = 64K
    SRAM1   (rw)  : ORIGIN = 0x20010000, LENGTH = 240K
    SRAM2   (rw)  : ORIGIN = 0x2004C000, LENGTH = 16K
}

_estack = ORIGIN(SRAM1) + LENGTH(SRAM1);
__stack_probe_size__ =
    DEFINED(FC_RUNTIME_PROBE_ENABLED) ? 0x8000 : 0;
__stack_probe_start__ = _estack - __stack_probe_size__;
__stack_probe_end__ = _estack;

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
        . = ALIGN(4);
        PROVIDE_HIDDEN(__preinit_array_start = .);
        KEEP(*(.preinit_array*))
        PROVIDE_HIDDEN(__preinit_array_end = .);
        . = ALIGN(4);
    } > FLASH

    .init_array (READONLY) :
    {
        . = ALIGN(4);
        PROVIDE_HIDDEN(__init_array_start = .);
        KEEP(*(SORT(.init_array.*)))
        KEEP(*(.init_array*))
        PROVIDE_HIDDEN(__init_array_end = .);
        . = ALIGN(4);
    } > FLASH

    .fini_array (READONLY) :
    {
        . = ALIGN(4);
        PROVIDE_HIDDEN(__fini_array_start = .);
        KEEP(*(SORT(.fini_array.*)))
        KEEP(*(.fini_array*))
        PROVIDE_HIDDEN(__fini_array_end = .);
        . = ALIGN(4);
    } > FLASH

    _sidata = LOADADDR(.data);

    .data :
    {
        . = ALIGN(4);
        _sdata = .;
        __data_start__ = _sdata;
        *(.data)
        *(.data*)
        *(.RamFunc)
        *(.RamFunc*)
        . = ALIGN(4);
        _edata = .;
        __data_end__ = _edata;
    } > SRAM1 AT > FLASH

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
    } > SRAM1

    /*
     * Optional one-shot acquisition buffer. It is filled completely before
     * UART draining and therefore must not consume DTCM or DMA-owned SRAM2.
     */
    .capture ALIGN(32) (NOLOAD) :
    {
        . = ALIGN(32);
        __capture_start__ = .;
        KEEP(*(.capture))
        KEEP(*(.capture.*))
        . = ALIGN(32);
        __capture_end__ = .;
    } > SRAM1

    /*
     * DTCM is single-cycle CPU memory and is intentionally reserved for the
     * solver context and complete finite-memory history. DMA cannot access it.
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
     * SRAM2 is DMA-visible. The application owns complete 32-byte cache lines
     * here, so maintenance for USART3 TX cannot affect unrelated objects.
     */
    .dma_buffer (NOLOAD) :
    {
        . = ALIGN(32);
        __dma_buffer_start__ = .;
        KEEP(*(.dma_buffer))
        KEEP(*(.dma_buffer.*))
        . = ALIGN(32);
        __dma_buffer_end__ = .;
    } > SRAM2

    ._user_heap_stack (NOLOAD) :
    {
        . = ALIGN(8);
        PROVIDE(end = .);
        PROVIDE(_end = .);
        . += _Min_Heap_Size;
        . += _Min_Stack_Size;
        . = ALIGN(8);
        __heap_stack_reservation_end__ = .;
    } > SRAM1

    .ARM.attributes 0 :
    {
        *(.ARM.attributes)
    }

    /DISCARD/ :
    {
        *(.note.GNU-stack)
        *(.comment)
    }
}

ASSERT(SIZEOF(.solver) <= LENGTH(DTCMRAM),
       "Solver history exceeds the 64 KiB DTCM bank")
ASSERT(SIZEOF(.dma_buffer) <= LENGTH(SRAM2),
       "DMA buffers exceed the dedicated 16 KiB SRAM2 bank")
ASSERT((ADDR(.capture) & 31) == 0,
       "Buffered capture section is not cache-line aligned")
ASSERT((ADDR(.dma_buffer) & 31) == 0,
       "DMA buffer section is not cache-line aligned")
ASSERT(__heap_stack_reservation_end__ <= __stack_probe_start__,
       "Static SRAM1 allocations overlap the 32 KiB stack probe")
ASSERT(__stack_probe_end__ == _estack,
       "The stack probe must end at the initial MSP")
