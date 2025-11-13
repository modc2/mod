import config from '../../config.json'
import Image from 'next/image'
import React from 'react'


export const Footer = () => {
  return (
    <footer className='mt-8 bg-black border-t border-green-500'>
      <div className='mx-auto flex max-w-7xl flex-col items-center px-6 py-6 lg:px-8'>
        <div className="mt-4 text-green-500 font-mono text-xs">
          © 
        </div>
      </div>
    </footer>
  )
}