#include <stddef.h>
#include <sys/types.h>

/*
 * No se redirige newlib a ningún periférico. Estas funciones satisfacen la ABI
 * sin habilitar semihosting ni convertir USART3 en salida de printf().
 */
int _close(int file)
{
    (void)file;
    return -1;
}

off_t _lseek(int file, off_t offset, int direction)
{
    (void)file;
    (void)offset;
    (void)direction;
    return (off_t)-1;
}

ssize_t _read(int file, void *buffer, size_t length)
{
    (void)file;
    (void)buffer;
    (void)length;
    return (ssize_t)-1;
}

ssize_t _write(int file, const void *buffer, size_t length)
{
    (void)file;
    (void)buffer;
    (void)length;
    return (ssize_t)-1;
}

